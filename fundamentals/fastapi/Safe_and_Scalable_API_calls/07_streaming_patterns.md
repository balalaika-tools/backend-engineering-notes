# Part 7: Streaming Response Patterns

> **Principle**: Streaming changes timeout semantics, concurrency accounting, and cancellation behavior.

---

## 1. How Streaming Changes Everything

### Non-Streaming (Buffered) Response

```
Client request
  ↓
Server processes (10-60s)
  ↓
Server sends complete response
  ↓
Connection closes
```

**Characteristics**:
- Single response body
- Clear "done" signal
- Predictable timeout behavior
- Semaphore released at response end

### Streaming Response

```
Client request
  ↓
Server sends first chunk (fast)
  ↓
Server sends chunk... (ongoing)
  ↓
Server sends chunk... (ongoing)
  ↓
Server sends final chunk
  ↓
Connection closes
```

**Characteristics**:
- Multiple chunks over time
- "Done" only when stream ends
- Timeout must be per-chunk, not total
- Semaphore held for entire stream duration
- Client can disconnect mid-stream

---

## 2. Key Differences

| Aspect | Buffered | Streaming |
|--------|----------|-----------|
| Response timing | One shot | Continuous |
| Timeout meaning | Total time | Per-chunk gap |
| Semaphore duration | Request time | Stream duration |
| Memory usage | Entire response | Chunk size |
| Cancellation | Clean | Must handle mid-stream |
| Client disconnect | Clear error | May be silent |

---

## 3. Timeout Behavior for Streaming

### The Problem

With buffered response:
```python
async with asyncio.timeout(30):  # 30s for entire response
    response = await client.post(...)
```

With streaming, this **breaks**:
```python
async with asyncio.timeout(30):  # WRONG: stream may take 60s total
    async with client.stream(...) as response:
        async for chunk in response.aiter_bytes():
            yield chunk  # Each chunk is fast, but total > 30s
```

### The Solution: Per-Chunk Timeout

HTTPX's `read` timeout already works per-chunk:
```python
client = httpx.AsyncClient(
    timeout=httpx.Timeout(read=30.0)  # 30s between chunks, not total
)
```

This is **safe for streaming** because the timer resets on each chunk.

---

## 4. Streaming Call Pattern

```python
import asyncio
import httpx
from typing import AsyncIterator
from aiolimiter import AsyncLimiter


# === GLOBAL PRIMITIVES ===
llm_sem = asyncio.Semaphore(50)
llm_rate = AsyncLimiter(60, 60)
client: httpx.AsyncClient = None


async def stream_llm(payload: dict) -> AsyncIterator[str]:
    """
    Streaming LLM call with proper concurrency handling.
    
    Key differences from buffered:
    - Semaphore held for entire stream duration
    - Queue timeout only covers stream initiation
    - No total timeout (per-chunk timeout via httpx)
    """
    
    # Queue timeout: covers acquiring resources and starting stream
    async with asyncio.timeout(5):
        async with llm_rate:
            async with llm_sem:
                # NOTE: Semaphore is held for ENTIRE stream duration
                
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/chat/completions",
                    json={**payload, "stream": True},
                ) as response:
                    response.raise_for_status()
                    
                    # Yield chunks as they arrive
                    # httpx read timeout applies per-chunk
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data != "[DONE]":
                                yield data
```

---

## 5. The Semaphore Duration Problem

### Issue

For buffered responses:
```
Semaphore acquired → request sent → response received → semaphore released
Duration: ~1-30s
```

For streaming responses:
```
Semaphore acquired → stream starts → chunks... chunks... → stream ends → semaphore released
Duration: ~10-120s (depending on output length)
```

**Streaming holds semaphore slots much longer**, reducing effective concurrency.

### Calculation

```
Buffered:
- Semaphore size: 50
- Average response time: 5s
- Effective throughput: 50 / 5s = 10 req/s

Streaming:
- Semaphore size: 50
- Average stream duration: 30s
- Effective throughput: 50 / 30s = 1.67 req/s
```

### Solutions

#### Option 1: Larger Semaphore for Streaming

```python
# Separate semaphores
buffered_sem = asyncio.Semaphore(50)
streaming_sem = asyncio.Semaphore(100)  # Larger for longer duration
```

#### Option 2: Weighted Semaphore

```python
from asyncio import Semaphore

class WeightedSemaphore:
    """Semaphore that accounts for expected duration."""
    
    def __init__(self, total_weight: int):
        self._sem = Semaphore(total_weight)
        self._total = total_weight
    
    async def acquire(self, weight: int = 1):
        for _ in range(weight):
            await self._sem.acquire()
    
    def release(self, weight: int = 1):
        for _ in range(weight):
            self._sem.release()


# Usage
sem = WeightedSemaphore(100)

# Buffered call: weight 1
await sem.acquire(1)
result = await buffered_call()
sem.release(1)

# Streaming call: weight 3 (expected 3x duration)
await sem.acquire(3)
async for chunk in stream_call():
    yield chunk
sem.release(3)
```

#### Option 3: Accept Lower Throughput

For many use cases, streaming's value (faster time-to-first-token) outweighs the throughput reduction. Simply size your infrastructure accordingly.

---

## 6. Client Disconnection Handling

### The Problem

Client can disconnect mid-stream:
- Browser closed
- Network failure
- Client timeout

Your server may not know immediately.

### Detection in FastAPI

```python
from fastapi import Request
from fastapi.responses import StreamingResponse


async def stream_with_disconnect_check(
    request: Request,
    payload: dict,
) -> AsyncIterator[str]:
    """Stream with client disconnect detection."""
    
    async with llm_sem:
        async with client.stream(...) as response:
            async for chunk in response.aiter_lines():
                # Check if client disconnected
                if await request.is_disconnected():
                    # Client gone - cleanup and exit
                    break
                
                yield chunk


@app.post("/stream")
async def stream_endpoint(request: Request, payload: dict):
    return StreamingResponse(
        stream_with_disconnect_check(request, payload),
        media_type="text/event-stream",
    )
```

### Cleanup on Disconnect

```python
async def stream_with_cleanup(
    request: Request,
    payload: dict,
) -> AsyncIterator[str]:
    """Stream with proper cleanup on any exit."""
    
    try:
        async with llm_sem:
            async with client.stream(...) as response:
                async for chunk in response.aiter_lines():
                    if await request.is_disconnected():
                        break
                    yield chunk
    
    finally:
        # Always runs: normal completion, disconnect, or error
        await record_stream_end()
```

---

## 7. Retry Logic for Streaming

### The Challenge

Streaming retries are complex:
- Can't retry mid-stream (client already received data)
- Retry only makes sense **before first chunk**
- After first chunk, failure = failure

### Strategy: Retry Only Stream Initiation

```python
async def stream_llm_with_retry(payload: dict) -> AsyncIterator[str]:
    """
    Retry only applies to stream initiation, not mid-stream failures.
    """
    
    for attempt in range(3):
        try:
            async with asyncio.timeout(5):  # Queue timeout
                async with llm_rate:
                    async with llm_sem:
                        
                        async with client.stream(...) as response:
                            response.raise_for_status()  # Check before streaming
                            
                            # Once we start yielding, no retry possible
                            first_chunk = True
                            async for line in response.aiter_lines():
                                if first_chunk:
                                    first_chunk = False
                                    # Past this point, retry is not possible
                                
                                yield line
                            
                            return  # Stream completed successfully
        
        except httpx.HTTPStatusError as e:
            if 500 <= e.response.status_code < 600 and attempt < 2:
                await asyncio.sleep(backoff(attempt))
                continue
            raise
        
        except httpx.ConnectError:
            if attempt < 2:
                await asyncio.sleep(backoff(attempt))
                continue
            raise
```

### Alternative: Wrapper with Pre-Check

```python
async def stream_llm_safe(payload: dict) -> AsyncIterator[str]:
    """
    Validate connection before streaming.
    Retry happens at validation, not stream.
    """
    
    # Phase 1: Validate (retryable)
    for attempt in range(3):
        try:
            # Quick validation request
            health = await client.get("https://api.openai.com/v1/models")
            health.raise_for_status()
            break
        except Exception:
            if attempt < 2:
                await asyncio.sleep(backoff(attempt))
                continue
            raise
    
    # Phase 2: Stream (not retryable)
    async for chunk in stream_llm(payload):
        yield chunk
```

---

## 8. Server-Sent Events (SSE) Pattern

### Complete SSE Implementation

```python
import asyncio
import json
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from aiolimiter import AsyncLimiter
from typing import AsyncIterator


# === GLOBAL PRIMITIVES ===
llm_sem = asyncio.Semaphore(50)
llm_rate = AsyncLimiter(60, 60)
client: httpx.AsyncClient = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=5.0,
            read=60.0,   # Per-chunk timeout for streaming
            write=10.0,
            pool=5.0,
        ),
        limits=httpx.Limits(
            max_connections=100,  # Higher for streaming
            max_keepalive_connections=30,
        ),
    )
    yield
    await client.aclose()


app = FastAPI(lifespan=lifespan)


async def generate_sse_stream(
    request: Request,
    payload: dict,
) -> AsyncIterator[str]:
    """
    Generate SSE-formatted stream.
    """
    
    try:
        async with asyncio.timeout(5):  # Queue timeout
            async with llm_rate:
                async with llm_sem:
                    
                    async with client.stream(
                        "POST",
                        "https://api.openai.com/v1/chat/completions",
                        json={**payload, "stream": True},
                        headers={"Authorization": f"Bearer {API_KEY}"},
                    ) as response:
                        response.raise_for_status()
                        
                        async for line in response.aiter_lines():
                            # Check disconnect
                            if await request.is_disconnected():
                                break
                            
                            if line.startswith("data: "):
                                data = line[6:]
                                if data == "[DONE]":
                                    yield "data: [DONE]\n\n"
                                    break
                                else:
                                    # Forward chunk
                                    yield f"data: {data}\n\n"
    
    except asyncio.TimeoutError:
        yield f"data: {json.dumps({'error': 'timeout'})}\n\n"
    
    except httpx.HTTPStatusError as e:
        yield f"data: {json.dumps({'error': f'upstream error: {e.response.status_code}'})}\n\n"
    
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


@app.post("/v1/chat/completions/stream")
async def stream_chat(request: Request, payload: dict):
    """
    SSE streaming endpoint.
    """
    return StreamingResponse(
        generate_sse_stream(request, payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
```

---

## 9. Metrics and Observability for Streaming

### Key Metrics

| Metric | What it measures |
|--------|------------------|
| `stream_duration_seconds` | Total stream time |
| `time_to_first_chunk_seconds` | Latency to first data |
| `chunks_per_stream` | Output size proxy |
| `stream_errors_total` | By error type |
| `client_disconnects_total` | Premature closures |
| `active_streams` | Current streaming connections |

### Implementation

```python
import time
from dataclasses import dataclass


@dataclass
class StreamMetrics:
    start_time: float
    first_chunk_time: float | None = None
    chunk_count: int = 0
    error: str | None = None
    client_disconnected: bool = False


async def stream_with_metrics(
    request: Request,
    payload: dict,
) -> AsyncIterator[str]:
    """Stream with comprehensive metrics."""
    
    metrics = StreamMetrics(start_time=time.time())
    
    try:
        async with llm_sem:
            async with client.stream(...) as response:
                async for chunk in response.aiter_lines():
                    if await request.is_disconnected():
                        metrics.client_disconnected = True
                        break
                    
                    # Record first chunk time
                    if metrics.first_chunk_time is None:
                        metrics.first_chunk_time = time.time()
                    
                    metrics.chunk_count += 1
                    yield chunk
    
    except Exception as e:
        metrics.error = type(e).__name__
        raise
    
    finally:
        duration = time.time() - metrics.start_time
        ttfc = (metrics.first_chunk_time - metrics.start_time
                if metrics.first_chunk_time else None)
        
        # Record metrics
        await record_stream_metrics(
            duration=duration,
            time_to_first_chunk=ttfc,
            chunks=metrics.chunk_count,
            error=metrics.error,
            disconnected=metrics.client_disconnected,
        )
```

---

## 10. Backpressure and Flow Control

### The Problem

If client is slow to consume chunks:
- Server buffers chunks in memory
- Memory usage grows
- Eventually OOM or degraded performance

### Solution: Bounded Buffer

FastAPI's `StreamingResponse` handles this via asyncio's flow control, but you can add explicit backpressure:

```python
async def stream_with_backpressure(
    payload: dict,
    max_buffer: int = 10,
) -> AsyncIterator[str]:
    """
    Stream with explicit backpressure handling.
    """
    
    buffer = asyncio.Queue(maxsize=max_buffer)
    
    async def producer():
        async with client.stream(...) as response:
            async for chunk in response.aiter_lines():
                await buffer.put(chunk)  # Blocks if buffer full
            await buffer.put(None)  # Sentinel
    
    # Start producer
    producer_task = asyncio.create_task(producer())
    
    try:
        while True:
            chunk = await buffer.get()
            if chunk is None:
                break
            yield chunk
    finally:
        producer_task.cancel()
```

---

## 11. Summary: Streaming vs Buffered

| Aspect | Buffered Pattern | Streaming Pattern |
|--------|------------------|-------------------|
| Timeout | Total request time | Per-chunk gap |
| Semaphore | Short duration | Long duration (size accordingly) |
| Retry | Any failure | Only before first chunk |
| Disconnect | Clear error | Must detect explicitly |
| Memory | Full response | Chunk size |
| Metrics | Simple | Need TTFC, duration, chunks |
| Backpressure | Automatic | Consider explicitly |

---

## Key Principles

1. **Streaming holds resources longer** — size semaphores accordingly
2. **Timeouts must be per-chunk** — use httpx `read` timeout
3. **Retries only work before first chunk** — design accordingly
4. **Client disconnect must be detected** — check `request.is_disconnected()`
5. **Track streaming-specific metrics** — TTFC is crucial
6. **Consider backpressure** — slow clients can cause problems

---

**Next**: [Part 8: Streaming Advanced Patterns](08_streaming_advanced.md) — multi-model routing, partial failure recovery, and streaming aggregation.
