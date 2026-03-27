# Part 8: Advanced Streaming Patterns

> **Principle**: Streaming complexity grows with fan-out, aggregation, and failure handling.

---

## 1. Stream Multiplexing: Multiple Concurrent Streams

### Use Case

User sends one request, you need to:
- Call multiple LLMs in parallel
- Stream results as they arrive
- Merge into single response stream

### Pattern: First-Response-Wins Streaming

```python
import asyncio
from typing import AsyncIterator


async def stream_first_responder(
    payload: dict,
    providers: list[str],
) -> AsyncIterator[str]:
    """
    Start streams to multiple providers, yield from first to respond.
    Cancel others once we commit to one.
    """
    
    streams = {}
    
    # Start all streams
    for provider in providers:
        streams[provider] = asyncio.create_task(
            get_first_chunk(provider, payload)
        )
    
    # Wait for first successful response
    winner = None
    winner_stream = None
    
    done, pending = await asyncio.wait(
        streams.values(),
        return_when=asyncio.FIRST_COMPLETED,
    )
    
    for task in done:
        try:
            provider, first_chunk, stream = task.result()
            winner = provider
            winner_stream = stream
            
            # Yield first chunk
            yield first_chunk
            break
        except Exception:
            continue
    
    # Cancel losers
    for task in pending:
        task.cancel()
    
    if winner_stream is None:
        raise Exception("All providers failed")
    
    # Continue with winner's stream
    async for chunk in winner_stream:
        yield chunk


async def get_first_chunk(provider: str, payload: dict):
    """
    Start stream, return first chunk + continuation.
    """
    stream = client.stream(...)
    
    async with stream as response:
        first_chunk = None
        
        async def continuation():
            async for chunk in response.aiter_lines():
                yield chunk
        
        async for chunk in response.aiter_lines():
            first_chunk = chunk
            break
        
        return provider, first_chunk, continuation()
```

---

## 2. Stream Aggregation: Merging Multiple Streams

### Use Case

Query multiple sources, merge results into single stream:
- Search across multiple databases
- Aggregate from multiple AI models
- Real-time data from multiple feeds

### Pattern: Interleaved Merge

```python
import asyncio
from typing import AsyncIterator


async def merge_streams(
    *streams: AsyncIterator[str],
    timeout: float = 30.0,
) -> AsyncIterator[tuple[int, str]]:
    """
    Merge multiple streams, yielding (source_index, chunk) as data arrives.
    """
    
    pending = {
        asyncio.create_task(stream.__anext__()): (i, stream)
        for i, stream in enumerate(streams)
    }
    
    while pending:
        done, _ = await asyncio.wait(
            pending.keys(),
            return_when=asyncio.FIRST_COMPLETED,
            timeout=timeout,
        )
        
        if not done:
            # Timeout - cancel remaining
            for task in pending:
                task.cancel()
            break
        
        for task in done:
            source_idx, stream = pending.pop(task)
            
            try:
                chunk = task.result()
                yield source_idx, chunk
                
                # Schedule next chunk from same stream
                pending[asyncio.create_task(stream.__anext__())] = (source_idx, stream)
            
            except StopAsyncIteration:
                # This stream is done
                pass
            
            except Exception as e:
                # This stream errored
                yield source_idx, f"ERROR: {e}"


# Usage
async def search_all_providers(query: str) -> AsyncIterator[str]:
    """Search multiple providers, merge results."""
    
    streams = [
        search_provider_a(query),
        search_provider_b(query),
        search_provider_c(query),
    ]
    
    async for source_idx, chunk in merge_streams(*streams):
        yield f"[{source_idx}] {chunk}"
```

---

## 3. Partial Failure Recovery

### Use Case

Stream is partially complete when error occurs:
- Network hiccup mid-stream
- Provider rate limit hit
- Temporary outage

### Pattern: Checkpoint and Resume

```python
from dataclasses import dataclass
from typing import AsyncIterator, Optional


@dataclass
class StreamCheckpoint:
    """Checkpoint for resumable streaming."""
    provider: str
    payload: dict
    chunks_received: int
    last_chunk_id: Optional[str]


async def resumable_stream(
    payload: dict,
    checkpoint: Optional[StreamCheckpoint] = None,
) -> AsyncIterator[str]:
    """
    Stream with checkpoint support for resumption.
    """
    
    if checkpoint:
        # Resume from checkpoint
        payload = {
            **payload,
            "resume_from": checkpoint.last_chunk_id,
        }
    
    chunk_count = checkpoint.chunks_received if checkpoint else 0
    last_chunk_id = checkpoint.last_chunk_id if checkpoint else None
    
    try:
        async with client.stream(...) as response:
            async for chunk in response.aiter_lines():
                # Extract chunk ID for checkpointing
                chunk_data = json.loads(chunk)
                last_chunk_id = chunk_data.get("id")
                chunk_count += 1
                
                yield chunk
    
    except Exception as e:
        # Save checkpoint for potential resume
        checkpoint = StreamCheckpoint(
            provider="openai",
            payload=payload,
            chunks_received=chunk_count,
            last_chunk_id=last_chunk_id,
        )
        
        # Could store checkpoint in Redis for client to resume
        await store_checkpoint(checkpoint)
        
        raise


async def stream_with_auto_resume(
    payload: dict,
    max_resumes: int = 3,
) -> AsyncIterator[str]:
    """
    Automatically resume on transient failures.
    """
    
    checkpoint = None
    
    for attempt in range(max_resumes + 1):
        try:
            async for chunk in resumable_stream(payload, checkpoint):
                yield chunk
            return  # Completed successfully
        
        except httpx.ReadTimeout:
            # Transient - try to resume
            checkpoint = await get_checkpoint()
            if attempt < max_resumes:
                await asyncio.sleep(backoff(attempt))
                continue
            raise
```

---

## 4. Stream Transformation Pipeline

### Use Case

Process stream chunks through transformation pipeline:
- Parse SSE format
- Extract content from JSON
- Apply filters or transformations

### Pattern: Async Pipeline

```python
from typing import AsyncIterator, Callable, TypeVar

T = TypeVar('T')
U = TypeVar('U')


async def map_stream(
    stream: AsyncIterator[T],
    func: Callable[[T], U],
) -> AsyncIterator[U]:
    """Map function over stream."""
    async for item in stream:
        yield func(item)


async def filter_stream(
    stream: AsyncIterator[T],
    predicate: Callable[[T], bool],
) -> AsyncIterator[T]:
    """Filter stream by predicate."""
    async for item in stream:
        if predicate(item):
            yield item


async def batch_stream(
    stream: AsyncIterator[T],
    size: int,
) -> AsyncIterator[list[T]]:
    """Batch stream items."""
    batch = []
    async for item in stream:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# Composable pipeline
async def process_llm_stream(payload: dict) -> AsyncIterator[str]:
    """
    Full processing pipeline for LLM stream.
    """
    
    # Raw stream
    raw_stream = stream_llm(payload)
    
    # Parse SSE
    parsed = map_stream(
        raw_stream,
        lambda line: json.loads(line[6:]) if line.startswith("data: ") else None,
    )
    
    # Filter nulls and done signals
    filtered = filter_stream(
        parsed,
        lambda x: x is not None and x != "[DONE]",
    )
    
    # Extract content
    content = map_stream(
        filtered,
        lambda x: x.get("choices", [{}])[0].get("delta", {}).get("content", ""),
    )
    
    # Filter empty
    non_empty = filter_stream(content, bool)
    
    async for chunk in non_empty:
        yield chunk
```

---

## 5. Stream Rate Limiting

### Use Case

Limit how fast you emit chunks to client:
- Prevent overwhelming slow clients
- Match playback speed for media
- Comply with downstream rate limits

### Pattern: Throttled Stream

```python
import asyncio
from typing import AsyncIterator


async def throttle_stream(
    stream: AsyncIterator[str],
    chunks_per_second: float,
) -> AsyncIterator[str]:
    """
    Throttle stream to max chunks per second.
    """
    
    min_interval = 1.0 / chunks_per_second
    last_emit = 0.0
    
    async for chunk in stream:
        now = asyncio.get_running_loop().time()
        elapsed = now - last_emit

        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)

        yield chunk
        last_emit = asyncio.get_running_loop().time()


async def adaptive_throttle(
    stream: AsyncIterator[str],
    initial_rate: float = 10.0,
) -> AsyncIterator[str]:
    """
    Adaptively throttle based on backpressure signals.
    """
    
    rate = initial_rate
    consecutive_slow = 0
    
    async for chunk in stream:
        try:
            # Try to yield with short timeout
            yield chunk
            consecutive_slow = 0
            rate = min(rate * 1.1, 100.0)  # Speed up
        
        except asyncio.QueueFull:
            consecutive_slow += 1
            rate = max(rate * 0.5, 1.0)  # Slow down
            await asyncio.sleep(1.0 / rate)
            yield chunk
```

---

## 6. Circuit Breaker for Streaming

### Challenge

Standard circuit breakers count failures per request.
With streaming, one long stream = one request, but may have many chunks.

### Pattern: Chunk-Aware Circuit Breaker

```python
import time
from dataclasses import dataclass


@dataclass
class StreamingCircuitState:
    failures: int = 0
    successes: int = 0
    last_failure: float = 0
    state: str = "closed"  # closed, open, half_open


class StreamingCircuitBreaker:
    """
    Circuit breaker that considers streaming behavior.
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 60.0,
        min_chunks_for_success: int = 10,
    ):
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout
        self.min_chunks = min_chunks_for_success
        self.state = StreamingCircuitState()
    
    async def execute(
        self,
        stream_func,
        *args,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Execute streaming function through circuit breaker.
        """
        
        if self.state.state == "open":
            if time.time() - self.state.last_failure > self.timeout:
                self.state.state = "half_open"
            else:
                raise CircuitBreakerOpen()
        
        chunk_count = 0
        error_occurred = False
        
        try:
            async for chunk in stream_func(*args, **kwargs):
                chunk_count += 1
                yield chunk
        
        except Exception as e:
            error_occurred = True
            self._record_failure()
            raise
        
        finally:
            if not error_occurred:
                # Success only if we got enough chunks
                if chunk_count >= self.min_chunks:
                    self._record_success()
                # Partial stream is ambiguous - don't count
    
    def _record_failure(self):
        self.state.failures += 1
        self.state.last_failure = time.time()
        
        if self.state.failures >= self.failure_threshold:
            self.state.state = "open"
    
    def _record_success(self):
        if self.state.state == "half_open":
            self.state.successes += 1
            if self.state.successes >= self.success_threshold:
                self.state.state = "closed"
                self.state.failures = 0
                self.state.successes = 0
        else:
            self.state.failures = 0
```

---

## 7. Streaming Load Shedding

### Challenge

Load shedding for streaming must consider:
- Current active streams (long-running)
- New stream requests
- System resources

### Pattern: Stream-Aware Admission

```python
from dataclasses import dataclass
from typing import AsyncIterator
import asyncio


@dataclass
class StreamAdmissionState:
    active_streams: int = 0
    total_chunks_per_second: float = 0.0
    
    max_streams: int = 50
    max_chunks_per_second: float = 1000.0


class StreamAdmissionController:
    """
    Admission control for streaming endpoints.
    """
    
    def __init__(self):
        self.state = StreamAdmissionState()
        self._lock = asyncio.Lock()
    
    async def admit(self) -> bool:
        """Check if new stream should be admitted."""
        async with self._lock:
            if self.state.active_streams >= self.state.max_streams:
                return False
            
            # Estimate: new stream will add ~20 chunks/sec
            estimated_load = self.state.total_chunks_per_second + 20
            if estimated_load > self.state.max_chunks_per_second:
                return False
            
            self.state.active_streams += 1
            return True
    
    async def release(self, chunks_emitted: int, duration: float):
        """Release admission slot with metrics."""
        async with self._lock:
            self.state.active_streams -= 1
            
            # Update estimated throughput
            if duration > 0:
                rate = chunks_emitted / duration
                # Exponential moving average
                self.state.total_chunks_per_second = (
                    0.9 * self.state.total_chunks_per_second +
                    0.1 * rate * self.state.active_streams
                )


# Usage
admission = StreamAdmissionController()


async def stream_with_admission(payload: dict) -> AsyncIterator[str]:
    """Stream with admission control."""
    
    if not await admission.admit():
        raise HTTPException(503, "Too many active streams")
    
    start = time.time()
    chunks = 0
    
    try:
        async for chunk in stream_llm(payload):
            chunks += 1
            yield chunk
    finally:
        await admission.release(chunks, time.time() - start)
```

---

## 8. Complete Production Streaming Endpoint

```python
import asyncio
import json
import time
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from aiolimiter import AsyncLimiter
from typing import AsyncIterator


# === GLOBAL STATE ===
llm_sem = asyncio.Semaphore(100)  # Higher for streaming
llm_rate = AsyncLimiter(60, 60)
client: httpx.AsyncClient = None
admission = StreamAdmissionController()
breaker = StreamingCircuitBreaker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=150, max_keepalive_connections=50),
    )
    yield
    await client.aclose()


app = FastAPI(lifespan=lifespan)


async def production_stream(
    request: Request,
    payload: dict,
) -> AsyncIterator[str]:
    """
    Production-grade streaming with all safeguards.
    """
    
    # 1. Admission control
    if not await admission.admit():
        raise HTTPException(503, "Service at capacity")
    
    start_time = time.time()
    chunk_count = 0
    first_chunk_time = None
    
    try:
        # 2. Queue timeout for resource acquisition
        async with asyncio.timeout(5):
            async with llm_rate:
                async with llm_sem:
                    
                    # 3. Circuit breaker
                    async for chunk in breaker.execute(
                        _raw_stream, payload
                    ):
                        # 4. Client disconnect check
                        if await request.is_disconnected():
                            break
                        
                        # 5. Metrics
                        if first_chunk_time is None:
                            first_chunk_time = time.time()
                        chunk_count += 1
                        
                        # 6. Format as SSE
                        yield f"data: {chunk}\n\n"
                    
                    yield "data: [DONE]\n\n"
    
    except asyncio.TimeoutError:
        yield f"data: {json.dumps({'error': 'timeout'})}\n\n"
    
    except CircuitBreakerOpen:
        yield f"data: {json.dumps({'error': 'service unavailable'})}\n\n"
    
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    finally:
        # 7. Release admission and record metrics
        duration = time.time() - start_time
        ttfc = first_chunk_time - start_time if first_chunk_time else None
        
        await admission.release(chunk_count, duration)
        await record_metrics(duration, ttfc, chunk_count)


async def _raw_stream(payload: dict) -> AsyncIterator[str]:
    """Raw vendor stream."""
    async with client.stream(
        "POST",
        "https://api.openai.com/v1/chat/completions",
        json={**payload, "stream": True},
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                yield line[6:]


@app.post("/v1/chat/stream")
async def chat_stream(request: Request, payload: dict):
    return StreamingResponse(
        production_stream(request, payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

---

## Summary: Streaming Complexity Ladder

| Level | Pattern | When to use |
|-------|---------|-------------|
| Basic | Single stream + semaphore | Simple proxy |
| Intermediate | + Disconnect detection + metrics | Production single-provider |
| Advanced | + Circuit breaker + admission | High availability |
| Expert | + Multi-stream + aggregation + resume | Multi-provider platforms |

---

## Key Principles

1. **Streaming is fundamentally different** — design for it explicitly
2. **Semaphore duration is much longer** — size appropriately
3. **Timeouts must be per-chunk** — not total time
4. **Client disconnect is silent** — detect explicitly
5. **Circuit breakers need chunk awareness** — partial streams are ambiguous
6. **Admission control must track active streams** — not just request rate
7. **Retries only work before streaming starts** — design for this limitation

---

**This completes the streaming patterns guide.**
