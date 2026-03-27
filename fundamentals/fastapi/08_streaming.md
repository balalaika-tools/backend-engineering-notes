# Streaming in FastAPI

How streaming works at the HTTP level, and how FastAPI exposes it. This guide covers **mechanics** — the fundamental building blocks. For production concurrency patterns (semaphores, circuit breakers, admission control), see the [Safe and Scalable API Calls streaming guides](Safe_and_Scalable_API_calls/07_streaming_patterns.md).

---

## 1. The Mental Model

### What "Streaming" Actually Means

In a normal HTTP response, the server builds the **entire** response body, then sends it:

```
Client request
  ↓
Server works (collects all data)
  ↓
Server sends complete body (Content-Length: 48392)
  ↓
Done
```

In a streaming response, the server sends the body **in pieces** as they become available:

```
Client request
  ↓
Server sends chunk 1 (available immediately)
  ↓
Server sends chunk 2 (available 200ms later)
  ↓
Server sends chunk 3 (available 500ms later)
  ↓
Server sends empty chunk (signals "done")
  ↓
Done
```

### How It Works at the HTTP Level: Chunked Transfer Encoding

HTTP/1.1 supports this via `Transfer-Encoding: chunked`. Instead of declaring `Content-Length` upfront, the server sends each piece prefixed with its size in hex:

```
HTTP/1.1 200 OK
Transfer-Encoding: chunked
Content-Type: text/plain

5\r\n
Hello\r\n
6\r\n
 World\r\n
0\r\n
\r\n
```

The `0\r\n\r\n` at the end signals "no more chunks." The client doesn't need to know the total size in advance.

### Why Streaming Matters

| Scenario | Without Streaming | With Streaming |
|----------|-------------------|----------------|
| 500MB file download | Server loads 500MB into memory, then sends | Server reads and sends 64KB at a time |
| LLM response (GPT, Claude) | User waits 10s for complete response | User sees first token in 200ms |
| CSV export (1M rows) | OOM crash or 30s wait | First rows appear in <1s |
| Real-time notifications | Polling every N seconds | Instant push as events happen |

The two key benefits:

1. **Reduced time-to-first-byte (TTFB)** — the client starts receiving data before the server finishes producing it
2. **Constant memory usage** — the server never holds the entire response in memory

---

## 2. StreamingResponse — How It Works Internally

### The Core Mechanism

`StreamingResponse` accepts any **iterable or async iterable** and sends each yielded value as an HTTP chunk:

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


async def generate_numbers():
    for i in range(5):
        yield f"Number: {i}\n"


@app.get("/numbers")
async def stream_numbers():
    return StreamingResponse(generate_numbers(), media_type="text/plain")
```

What happens under the hood:

```
1. FastAPI calls StreamingResponse(generator, media_type="text/plain")
2. Starlette sets Transfer-Encoding: chunked (no Content-Length)
3. For each `yield`:
   a. The yielded string/bytes becomes one HTTP chunk
   b. Starlette writes it to the ASGI send() interface
   c. Uvicorn flushes it to the TCP socket
4. When the generator exhausts, Starlette sends the final empty chunk
```

### Async vs Sync Generators

`StreamingResponse` accepts both. But they behave very differently:

```python
# Async generator — runs on the event loop, non-blocking
async def async_gen():
    for i in range(100):
        await asyncio.sleep(0.1)  # non-blocking sleep
        yield f"chunk {i}\n"

# Sync generator — runs in a threadpool executor
def sync_gen():
    for i in range(100):
        time.sleep(0.1)  # blocking sleep (but safe — runs in thread)
        yield f"chunk {i}\n"
```

When you pass a **sync generator**, Starlette wraps it in `run_in_executor` so it doesn't block the event loop. This works, but has overhead. Prefer async generators for streaming.

### media_type

The `media_type` parameter sets the `Content-Type` header. Common values:

| media_type | Use Case |
|------------|----------|
| `text/plain` | Plain text streams |
| `text/event-stream` | Server-Sent Events (SSE) |
| `application/json` | Streaming JSON (NDJSON) |
| `text/csv` | CSV export |
| `application/octet-stream` | Binary file download |
| `application/pdf` | PDF file download |

### Custom Headers

```python
@app.get("/export")
async def export_data():
    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=export.csv",
            "Cache-Control": "no-cache",
        },
    )
```

---

## 3. When to Use Streaming

### Use Streaming

- **Large file downloads** — don't load entire files into memory
- **Large data exports** — CSV/JSON exports with millions of rows
- **LLM token output** — show tokens as they're generated
- **Real-time events** — live logs, notifications, progress updates
- **Proxying upstream streams** — forwarding OpenAI/Anthropic responses

### Don't Use Streaming

- **Small JSON responses** — `return {"status": "ok"}` is fine
- **Responses that need post-processing** — if you need to sort, paginate, or transform the complete result before sending
- **Responses where you need the total count** — `Content-Length` isn't available with chunked encoding
- **When you need HTTP status based on body content** — status code is sent with the first chunk, before you've processed all data

---

## 4. Server-Sent Events (SSE)

### What SSE Is

SSE is a **protocol on top of HTTP** for pushing events from server to client. It's not a separate transport like WebSocket — it's just a streaming HTTP response with a specific text format.

```
Regular streaming:     raw bytes/text, any format, client must parse
SSE:                   structured events with type/data/id fields, built-in browser API
WebSocket:             bidirectional, binary or text, separate protocol
```

### The SSE Wire Format

An SSE response is `Content-Type: text/event-stream`. Each event is a block of `field: value` lines, terminated by **two newlines** (`\n\n`):

```
data: This is a message

data: This is another message

event: custom_type
data: {"key": "value"}

id: 42
event: update
data: line one
data: line two
retry: 5000

```

Field reference:

| Field | Purpose | Required |
|-------|---------|----------|
| `data:` | The event payload. Multiple `data:` lines are joined with `\n` | Yes |
| `event:` | Event type name. Default is `"message"` if omitted | No |
| `id:` | Event ID. Browser sends `Last-Event-ID` header on reconnect | No |
| `retry:` | Reconnection interval in milliseconds | No |

### How Browsers Consume SSE

Browsers have a built-in `EventSource` API:

```javascript
const source = new EventSource("/events");

// Default "message" events
source.onmessage = (event) => {
    console.log(event.data);
};

// Named events
source.addEventListener("update", (event) => {
    console.log("Update:", event.data);
});

// Connection management
source.onerror = (event) => {
    console.log("Connection lost, browser will auto-reconnect");
};
```

Key `EventSource` behaviors:

- **Auto-reconnects** on connection loss (with exponential backoff)
- Sends `Last-Event-ID` header on reconnect (if you set `id:` fields)
- Only supports **GET** requests (limitation — use `fetch()` for POST)
- Cannot set custom headers (no `Authorization` — use query params or cookies)

### SSE vs Fetch for Streaming

For modern applications (especially LLM chat UIs), `fetch()` with a readable stream is more flexible:

```javascript
const response = await fetch("/chat/stream", {
    method: "POST",
    headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer token123",
    },
    body: JSON.stringify({ message: "Hello" }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const text = decoder.decode(value);
    // Parse SSE format manually or use a library
    for (const line of text.split("\n")) {
        if (line.startsWith("data: ")) {
            const data = line.slice(6);
            if (data !== "[DONE]") {
                console.log(JSON.parse(data));
            }
        }
    }
}
```

---

## 5. SSE Implementation in FastAPI

### Complete Working Example

```python
import asyncio
import json
import time
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()


def format_sse(data: str, event: str | None = None, id: str | None = None, retry: int | None = None) -> str:
    """Format a single SSE event according to the spec."""
    lines = []
    if id is not None:
        lines.append(f"id: {id}")
    if event is not None:
        lines.append(f"event: {event}")
    if retry is not None:
        lines.append(f"retry: {retry}")
    # data can be multiline — each line needs its own `data:` prefix
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    lines.append("")  # trailing newline
    lines.append("")  # empty line = event boundary
    return "\n".join(lines)


async def event_generator(request: Request):
    """Generate SSE events with proper formatting."""
    event_id = 0

    # Send retry interval on first event
    yield format_sse(
        data="connected",
        event="status",
        retry=5000,
    )

    while True:
        # Check for client disconnect
        if await request.is_disconnected():
            break

        event_id += 1
        event_data = json.dumps({
            "id": event_id,
            "timestamp": time.time(),
            "message": f"Event #{event_id}",
        })

        yield format_sse(
            data=event_data,
            event="update",
            id=str(event_id),
        )

        await asyncio.sleep(1)


@app.get("/events")
async def sse_endpoint(request: Request):
    return StreamingResponse(
        event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

### What the Wire Looks Like

```
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
Transfer-Encoding: chunked

retry: 5000
event: status
data: connected

id: 1
event: update
data: {"id": 1, "timestamp": 1700000000.0, "message": "Event #1"}

id: 2
event: update
data: {"id": 2, "timestamp": 1700000001.0, "message": "Event #2"}

```

---

## 6. Streaming from Upstream APIs

### The Pattern: Proxy an Upstream Stream

When your FastAPI server calls an upstream API (OpenAI, Anthropic, etc.) that returns a streaming response, you need to **forward chunks as they arrive** rather than buffering the entire response.

### Using httpx.stream()

```python
import httpx
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

client: httpx.AsyncClient = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
    )
    yield
    await client.aclose()


app = FastAPI(lifespan=lifespan)


async def proxy_openai_stream(payload: dict, request: Request):
    """
    Forward an OpenAI streaming response chunk by chunk.

    httpx.stream() does NOT buffer the response body — it yields
    chunks as the upstream server sends them.
    """
    async with client.stream(
        "POST",
        "https://api.openai.com/v1/chat/completions",
        json={**payload, "stream": True},
        headers={"Authorization": f"Bearer {API_KEY}"},
    ) as response:
        response.raise_for_status()

        async for line in response.aiter_lines():
            if await request.is_disconnected():
                break

            if line.startswith("data: "):
                # Forward the SSE event as-is
                yield f"{line}\n\n"


@app.post("/chat")
async def chat(request: Request):
    payload = await request.json()
    return StreamingResponse(
        proxy_openai_stream(payload, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

### Key httpx Methods for Streaming

| Method | What It Yields | Use Case |
|--------|---------------|----------|
| `response.aiter_bytes()` | Raw bytes chunks | Binary data, files |
| `response.aiter_text()` | Decoded text chunks | Text streams |
| `response.aiter_lines()` | Lines (split by `\n`) | SSE, NDJSON, line-delimited formats |
| `response.aiter_raw()` | Raw bytes without decompression | Proxying without re-encoding |

### Common Mistake: Not Using stream()

```python
# ❌ Wrong — downloads entire response into memory first
response = await client.post(url, json=payload)
# At this point the entire body is in memory

# ✅ Correct — streams chunk by chunk
async with client.stream("POST", url, json=payload) as response:
    async for chunk in response.aiter_lines():
        yield chunk
```

The difference: `await client.post()` reads the complete response body before returning. `client.stream()` returns as soon as headers arrive, then you consume the body incrementally.

---

## 7. File Downloads

### Streaming a Large File Without Loading Into Memory

```python
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse

app = FastAPI()

CHUNK_SIZE = 64 * 1024  # 64KB chunks


async def file_chunk_generator(file_path: Path):
    """Read file in chunks — never holds more than CHUNK_SIZE in memory."""
    async with aiofiles.open(file_path, "rb") as f:
        while chunk := await f.read(CHUNK_SIZE):
            yield chunk


@app.get("/download/{filename}")
async def download_file(filename: str):
    file_path = Path("/data/exports") / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return StreamingResponse(
        file_chunk_generator(file_path),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Content-Length is optional but helps clients show progress bars
            "Content-Length": str(file_path.stat().st_size),
        },
    )
```

### StreamingResponse vs FileResponse

```python
# FileResponse — let Starlette handle everything
# Uses sendfile() syscall when possible (zero-copy, kernel-level)
@app.get("/download/v1/{filename}")
async def download_v1(filename: str):
    return FileResponse(
        path=f"/data/exports/{filename}",
        filename=filename,              # Sets Content-Disposition
        media_type="application/octet-stream",
    )


# StreamingResponse — when you need to transform data on the fly
# No sendfile() optimization, but you control every byte
@app.get("/download/v2/{filename}")
async def download_v2(filename: str):
    async def generate():
        async with aiofiles.open(f"/data/exports/{filename}", "rb") as f:
            while chunk := await f.read(64 * 1024):
                # You could encrypt, compress, or transform here
                yield chunk

    return StreamingResponse(generate(), media_type="application/octet-stream")
```

**Rule of thumb**: Use `FileResponse` for serving files as-is. Use `StreamingResponse` when you need to process the data before sending (encryption, on-the-fly CSV generation, dynamic content).

### Content-Disposition Explained

```python
# Inline — browser displays the file (images, PDFs in browser)
"Content-Disposition": "inline"

# Attachment — browser downloads the file with a save dialog
"Content-Disposition": 'attachment; filename="report.csv"'

# Filename with special characters
"Content-Disposition": "attachment; filename*=UTF-8''r%C3%A9port.csv"
```

---

## 8. File Uploads — Streaming the Request Body

### How UploadFile Works

`UploadFile` does **not** load the entire file into memory by default. It uses a `SpooledTemporaryFile` that stays in memory up to 1MB, then spills to disk:

```python
from fastapi import FastAPI, UploadFile

app = FastAPI()


@app.post("/upload")
async def upload_file(file: UploadFile):
    # file.file is a SpooledTemporaryFile
    # For files > 1MB, data is on disk, not in RAM
    contents = await file.read()  # But THIS loads it all into memory
    return {"size": len(contents)}
```

### Chunked Reading — The Right Way

```python
@app.post("/upload/large")
async def upload_large_file(file: UploadFile):
    """Process a large upload without loading it all into memory."""
    total_size = 0
    chunk_size = 64 * 1024  # 64KB

    # Write to destination in chunks
    async with aiofiles.open(f"/data/uploads/{file.filename}", "wb") as dest:
        while chunk := await file.read(chunk_size):
            await dest.write(chunk)
            total_size += len(chunk)

    return {"filename": file.filename, "size": total_size}
```

### Streaming Request Body Directly

For maximum control, you can read the raw request body as a stream without `UploadFile`:

```python
from fastapi import Request


@app.post("/upload/raw")
async def upload_raw(request: Request):
    """Stream the raw request body — no parsing, no temp files."""
    total_size = 0

    async with aiofiles.open("/data/uploads/raw_upload.bin", "wb") as f:
        async for chunk in request.stream():
            await f.write(chunk)
            total_size += len(chunk)

    return {"size": total_size}
```

### Comparison

| Approach | Memory Usage | Features |
|----------|-------------|----------|
| `await file.read()` | Entire file in memory | Filename, content_type available |
| `await file.read(chunk_size)` in loop | One chunk at a time | Filename, content_type available |
| `request.stream()` | One chunk at a time | No parsing — raw bytes only |

---

## 9. Client Disconnect Detection

### Why This Matters

When streaming a long response (LLM output, real-time feed, large export), the client can disconnect at any time:

- User closes the browser tab
- Mobile app goes to background
- Network drops
- Client-side timeout fires

Without detection, your server keeps generating data for nobody — wasting CPU, memory, and upstream API costs (LLM tokens are expensive).

### Using request.is_disconnected()

```python
from fastapi import Request
from fastapi.responses import StreamingResponse


async def expensive_stream(request: Request):
    """Stream that checks for client disconnect."""
    for i in range(10_000):
        # Periodic disconnect check
        if await request.is_disconnected():
            # Client is gone — stop generating
            print(f"Client disconnected after {i} chunks")
            return

        yield f"data: {generate_expensive_chunk(i)}\n\n"
        await asyncio.sleep(0.05)


@app.get("/stream")
async def stream_endpoint(request: Request):
    return StreamingResponse(
        expensive_stream(request),
        media_type="text/event-stream",
    )
```

### Cleanup on Disconnect

Use `try/finally` to ensure resources are released when the stream ends for any reason:

```python
async def stream_with_cleanup(request: Request):
    """Cleanup runs whether client disconnects, errors, or stream completes."""
    resource = await acquire_expensive_resource()

    try:
        async for item in resource.stream():
            if await request.is_disconnected():
                break
            yield format_sse(item)
    finally:
        # Always runs: normal completion, disconnect, or exception
        await resource.release()
        await log_stream_end()
```

### Caveat: is_disconnected() Is Not Instant

`request.is_disconnected()` checks whether the ASGI server has received a `disconnect` event. This detection depends on:

- **Uvicorn's** polling interval
- **TCP keepalive** settings
- Whether the client sent a proper `FIN` or just disappeared

In practice, there can be a delay of a few seconds between the actual disconnect and detection. Don't rely on it for sub-second cleanup.

---

## 10. Backpressure

### What Backpressure Means

When the server produces data faster than the client can consume it:

```
Server: yield chunk → yield chunk → yield chunk → yield chunk → ...
                                                          ↑
Client: read chunk ................. read chunk ...........
         (slow consumer)
```

The chunks pile up in buffers. Without backpressure, the server eventually runs out of memory.

### How asyncio Handles It

The flow control works through several layers:

```
Generator yield
  ↓
Starlette's StreamingResponse calls ASGI send()
  ↓
Uvicorn writes to asyncio transport
  ↓
asyncio transport writes to OS TCP send buffer
  ↓
OS TCP send buffer fills up → TCP window closes
  ↓
asyncio transport.write() starts returning backpressure signals
  ↓
Uvicorn's await send() blocks (pauses the coroutine)
  ↓
Your generator's yield pauses naturally
```

The key insight: **`yield` in an async generator is an implicit suspension point**. When the downstream can't accept more data, the `await` inside Starlette's send loop blocks, which means your generator doesn't resume, which means you stop producing data. This is automatic backpressure.

### When Automatic Backpressure Isn't Enough

If you're producing data from a separate task (not directly in the generator), you need explicit coordination:

```python
async def stream_with_bounded_buffer(request: Request):
    """
    Producer runs independently from the HTTP stream.
    Queue provides explicit backpressure.
    """
    buffer: asyncio.Queue[str | None] = asyncio.Queue(maxsize=20)

    async def producer():
        try:
            async for item in external_data_source():
                await buffer.put(item)  # Blocks when buffer is full
            await buffer.put(None)  # Sentinel: stream complete
        except Exception:
            await buffer.put(None)

    task = asyncio.create_task(producer())

    try:
        while True:
            if await request.is_disconnected():
                break
            chunk = await buffer.get()
            if chunk is None:
                break
            yield f"data: {chunk}\n\n"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
```

Without `maxsize`, the queue grows unbounded and you lose backpressure.

---

## 11. Response Type Comparison

### When to Use What

| Type | Memory | Speed | Use Case |
|------|--------|-------|----------|
| `Response` | Entire body in memory | Fast for small responses | JSON, HTML, small payloads |
| `JSONResponse` | Entire body in memory | Serializes with `json.dumps` | API responses (FastAPI default) |
| `FileResponse` | Kernel-level sendfile | Fastest for static files | Serving existing files from disk |
| `StreamingResponse` | One chunk at a time | Depends on generator | Large data, real-time streams, proxying |
| `HTMLResponse` | Entire body in memory | Fast | HTML pages |

### Decision Flowchart

```
Is it a file on disk?
  ├── Yes → FileResponse
  └── No
      ├── Is the response small (<1MB) and fully available?
      │   ├── Yes → JSONResponse / Response
      │   └── No
      │       └── StreamingResponse
      └── Is it real-time / ongoing?
          └── Yes → StreamingResponse (with SSE format for browser clients)
```

### StreamingResponse vs SSE — They're Not Separate Things

SSE is not a different response class. It's `StreamingResponse` with:
- `media_type="text/event-stream"`
- Data formatted as `data: ...\n\n`
- `Cache-Control: no-cache` header

```python
# "SSE endpoint" is just a StreamingResponse with specific format and headers
return StreamingResponse(
    my_generator(),
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache"},
)
```

---

## 12. Common Mistakes

### Forgetting `\n\n` in SSE

```python
# ❌ Wrong — client never receives events (no event boundary)
yield f"data: {message}\n"

# ❌ Wrong — events run together
yield f"data: {message}"

# ✅ Correct — double newline terminates each event
yield f"data: {message}\n\n"
```

Without `\n\n`, the SSE parser on the client side never sees a complete event. Data accumulates in the buffer forever.

### Not Setting Cache-Control

```python
# ❌ Wrong — proxies and browsers may buffer the entire stream
return StreamingResponse(generator(), media_type="text/event-stream")

# ✅ Correct — prevents buffering at every layer
return StreamingResponse(
    generator(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # nginx
    },
)
```

Without `Cache-Control: no-cache`, an HTTP cache or CDN might buffer your stream and deliver it all at once when complete — defeating the entire purpose.

### Nginx/Proxy Buffering

Nginx buffers responses by default. For streaming, you must disable it:

```nginx
# Option 1: Disable globally for a location
location /stream {
    proxy_buffering off;
    proxy_cache off;
}

# Option 2: Let the app control it per-response (using X-Accel-Buffering header)
# FastAPI sets: X-Accel-Buffering: no
# Nginx respects this header automatically
```

Other proxies to watch for:

| Proxy | How to Disable Buffering |
|-------|--------------------------|
| nginx | `proxy_buffering off;` or `X-Accel-Buffering: no` header |
| AWS ALB | Chunked responses stream by default (no change needed) |
| Cloudflare | Streams by default, but may buffer for bot detection |
| HAProxy | `option http-no-delay` |
| Traefik | Streams by default (no change needed) |

### Using yield in a Sync def Endpoint

```python
# ❌ Wrong — sync endpoint with yield doesn't create a streaming response
@app.get("/wrong")
def wrong():
    yield "this"
    yield "does not"
    yield "stream"

# ✅ Correct — return a StreamingResponse wrapping a generator
@app.get("/correct")
def correct():
    def generate():
        yield "this "
        yield "does "
        yield "stream"
    return StreamingResponse(generate(), media_type="text/plain")
```

Using `yield` directly in a FastAPI endpoint makes it a **dependency with yield** (setup/teardown), not a streaming response. You must always wrap generators in `StreamingResponse`.

### Sending the Error After Streaming Starts

```python
# ❌ Problem — status code is already sent with the first chunk
async def broken_stream():
    yield "Starting...\n"
    # ... later ...
    if error:
        # Too late to send 500 — client already got 200 with first chunk
        raise HTTPException(status_code=500, detail="Something failed")

# ✅ Better — validate before streaming begins
@app.get("/export")
async def export():
    # Validation happens before StreamingResponse is returned
    data_source = await get_data_source()
    if not data_source.is_available():
        raise HTTPException(status_code=503, detail="Data source unavailable")

    # Only start streaming after validation passes
    return StreamingResponse(data_source.stream(), media_type="text/csv")
```

Once the first byte is sent, the HTTP status code is locked at 200 (or whatever you set). You cannot change it mid-stream. Design your endpoints to fail fast **before** returning the `StreamingResponse`.

### Buffering the Entire Stream Before Sending

```python
# ❌ Wrong — collects all chunks, then sends as one response (defeats streaming)
@app.get("/bad")
async def bad():
    chunks = []
    async for chunk in generate_data():
        chunks.append(chunk)
    return Response(content="".join(chunks))

# ✅ Correct — yields chunks as they're produced
@app.get("/good")
async def good():
    return StreamingResponse(generate_data(), media_type="text/plain")
```

---

## Quick Reference

### Minimal SSE Endpoint

```python
async def event_stream(request: Request):
    while not await request.is_disconnected():
        data = await get_next_event()
        yield f"data: {json.dumps(data)}\n\n"

@app.get("/events")
async def events(request: Request):
    return StreamingResponse(
        event_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

### Minimal File Download

```python
@app.get("/download/{name}")
async def download(name: str):
    return FileResponse(f"/data/{name}", filename=name)
```

### Minimal Upstream Proxy

```python
async def proxy(request: Request):
    async with client.stream("POST", upstream_url, json=payload) as resp:
        async for line in resp.aiter_lines():
            if await request.is_disconnected():
                break
            yield f"{line}\n"

@app.post("/proxy")
async def proxy_endpoint(request: Request):
    return StreamingResponse(proxy(request), media_type="text/event-stream")
```

---

## Key Takeaways

1. **Streaming = chunked transfer encoding** — the server sends pieces, not the whole body
2. **StreamingResponse wraps a generator** — each `yield` becomes an HTTP chunk
3. **SSE is just a format convention** — `data: ...\n\n` over `text/event-stream`, not a separate protocol class
4. **Always set `Cache-Control: no-cache` and `X-Accel-Buffering: no`** — or proxies will buffer your stream
5. **Check `request.is_disconnected()`** — stop wasting resources when nobody is listening
6. **Validate before streaming** — you can't change the status code after the first byte
7. **Use `FileResponse` for files on disk** — it uses zero-copy `sendfile()` under the hood
8. **Backpressure is mostly automatic** — `yield` naturally pauses when downstream is slow
