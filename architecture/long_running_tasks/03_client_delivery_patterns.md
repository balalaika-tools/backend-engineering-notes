# Part 3: Client Delivery Patterns

How the client gets the result back after submitting a long-running task. Every pattern starts the same way: the client sends a request, gets a `202 Accepted` with a job ID, and then... what?

---

## Pattern 1: WebSocket

The client opens a persistent bidirectional connection. The server pushes the result (and optionally progress updates) when ready.

### Flow

```
Client                              Server (Orchestrator)
  │                                      │
  │── POST /jobs {payload} ──►           │
  │◄── 202 {job_id} ──                   │── dispatch to worker
  │                                      │
  │── WS CONNECT /ws/jobs/{job_id} ──►   │
  │◄── connection established ──         │
  │                                      │
  │    (connection held open)            │    (worker processing)
  │                                      │
  │◄── WS: {"progress": 30} ──           │◄── worker heartbeat
  │◄── WS: {"progress": 70} ──           │◄── worker heartbeat
  │◄── WS: {"status": "done",            │◄── worker callback
  │         "result": {...}} ──          │
  │                                      │
  │── WS CLOSE ──►                       │
```

### Server Implementation

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncio

app = FastAPI()

# In-memory or Redis-backed: maps job_id → set of connected WebSockets
_subscribers: dict[str, set[WebSocket]] = {}

@app.websocket("/ws/jobs/{job_id}")
async def job_websocket(ws: WebSocket, job_id: str):
    await ws.accept()
    _subscribers.setdefault(job_id, set()).add(ws)

    try:
        # Check if already complete (late connection)
        status = await get_job_status(job_id)
        if status["state"] in ("succeeded", "failed"):
            await ws.send_json(status)
            return

        # Hold connection open, waiting for updates
        # The push happens in notify_subscribers() below
        while True:
            # Keep-alive ping or wait for client messages
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    finally:
        _subscribers.get(job_id, set()).discard(ws)


async def notify_subscribers(job_id: str, message: dict):
    """Called by the orchestrator when a worker sends an update."""
    for ws in list(_subscribers.get(job_id, set())):
        try:
            await ws.send_json(message)
        except Exception:
            _subscribers[job_id].discard(ws)
```

### Client Implementation (JavaScript)

```javascript
async function submitAndWait(payload) {
    // 1. Submit the job
    const res = await fetch('/jobs', { method: 'POST', body: JSON.stringify(payload) });
    const { job_id } = await res.json();

    // 2. Connect WebSocket for result
    return new Promise((resolve, reject) => {
        const ws = new WebSocket(`wss://api.example.com/ws/jobs/${job_id}`);

        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            if (msg.status === 'done') {
                resolve(msg.result);
                ws.close();
            } else if (msg.status === 'failed') {
                reject(new Error(msg.error));
                ws.close();
            } else if (msg.progress) {
                updateProgressBar(msg.progress);
            }
        };

        ws.onerror = () => reject(new Error('WebSocket error'));

        // Timeout safety net
        setTimeout(() => { ws.close(); reject(new Error('Timeout')); }, 600_000);
    });
}
```

### Characteristics

| Aspect | Detail |
|--------|--------|
| **Latency** | Near-instant — push-based |
| **Server cost** | High — one open connection per waiting client |
| **Client complexity** | Medium — must handle reconnection, missed messages |
| **Progress updates** | Native — bidirectional channel |
| **Infrastructure** | Requires WebSocket-capable load balancer (ALB, nginx, Cloudflare) |
| **Scalability** | Limited by max open connections (typically 10K–100K per server) |
| **Best for** | Interactive UIs, real-time dashboards, when progress updates matter |

### Edge Cases

- **Client disconnects and reconnects**: Must handle "catch-up" — on reconnect, send current status and any missed updates. Store updates in Redis sorted set or similar.
- **Load balancer timeout**: AWS ALB's idle timeout defaults to 60s and is configurable up to 4000s. Send periodic pings to keep the connection alive.
- **Multiple tabs**: Same job, multiple WebSocket connections. Use Redis pub/sub to fan out.

---

## Pattern 2: Server-Sent Events (SSE)

Unidirectional server-to-client push over HTTP. Simpler than WebSocket — no upgrade handshake, works through most proxies, automatic reconnection built into the browser API.

### Flow

```
Client                              Server
  │                                      │
  │── POST /jobs {payload} ──►           │
  │◄── 202 {job_id} ──                   │
  │                                      │
  │── GET /jobs/{job_id}/stream ──►      │
  │◄── Content-Type: text/event-stream   │
  │◄── data: {"progress": 30}            │
  │◄── data: {"progress": 70}            │
  │◄── data: {"status": "done", ...}     │
  │◄── (connection closed by server)     │
```

### Server Implementation

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import asyncio
import json

@app.get("/jobs/{job_id}/stream")
async def job_stream(job_id: str):
    async def event_generator():
        while True:
            status = await get_job_status(job_id)

            yield f"data: {json.dumps(status)}\n\n"

            if status["state"] in ("succeeded", "failed"):
                return

            await asyncio.sleep(2)  # Poll interval

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### Client Implementation (JavaScript)

```javascript
const evtSource = new EventSource(`/jobs/${jobId}/stream`);

evtSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.status === 'done') {
        handleResult(data.result);
        evtSource.close();
    }
};

// EventSource auto-reconnects on network failure — built into the spec
```

### Characteristics

| Aspect | Detail |
|--------|--------|
| **Latency** | Low — push-based with small polling delay on server side |
| **Server cost** | Medium — one open HTTP connection per client (lighter than WS) |
| **Client complexity** | Low — `EventSource` API handles reconnection |
| **Progress updates** | Yes — natural fit |
| **Infrastructure** | Works through any HTTP proxy/LB (no upgrade needed) |
| **Best for** | Progress updates, when you don't need client-to-server messages |

---

## Pattern 3: Short Polling

The client periodically hits a status endpoint. Simplest pattern to implement; most wasteful of resources.

### Flow

```
Client                              Server
  │                                      │
  │── POST /jobs {payload} ──►           │
  │◄── 202 {job_id} ──                   │
  │                                      │
  │── GET /jobs/{job_id} ──►             │
  │◄── {"status": "pending"} ──          │
  │                                      │
  │    (wait 2 seconds)                  │
  │                                      │
  │── GET /jobs/{job_id} ──►             │
  │◄── {"status": "running",             │
  │     "progress": 45} ──               │
  │                                      │
  │    (wait 2 seconds)                  │
  │                                      │
  │── GET /jobs/{job_id} ──►             │
  │◄── {"status": "succeeded",           │
  │     "result": {...}} ──              │
```

### Client Implementation

```python
import httpx
import asyncio

async def poll_for_result(base_url: str, job_id: str, interval: float = 2.0, timeout: float = 600.0):
    deadline = asyncio.get_running_loop().time() + timeout

    async with httpx.AsyncClient() as client:
        while asyncio.get_running_loop().time() < deadline:
            resp = await client.get(f"{base_url}/jobs/{job_id}")
            data = resp.json()

            if data["status"] == "succeeded":
                return data["result"]
            elif data["status"] == "failed":
                raise RuntimeError(data.get("error", "Task failed"))

            await asyncio.sleep(interval)

    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")
```

### With Exponential Backoff

```python
async def poll_with_backoff(base_url: str, job_id: str, timeout: float = 600.0):
    interval = 1.0  # Start at 1 second
    max_interval = 30.0
    deadline = asyncio.get_running_loop().time() + timeout

    async with httpx.AsyncClient() as client:
        while asyncio.get_running_loop().time() < deadline:
            resp = await client.get(f"{base_url}/jobs/{job_id}")
            data = resp.json()

            if data["status"] in ("succeeded", "failed"):
                return data

            await asyncio.sleep(interval)
            interval = min(interval * 1.5, max_interval)  # Backoff

    raise TimeoutError(f"Job {job_id} timed out")
```

### Characteristics

| Aspect | Detail |
|--------|--------|
| **Latency** | Medium — bounded by polling interval |
| **Server cost** | Medium — repeated HTTP requests, but no held connections |
| **Client complexity** | Lowest — just a loop |
| **Progress updates** | Yes — each poll returns current status |
| **Infrastructure** | Any HTTP stack, no special requirements |
| **Best for** | Server-to-server integrations, simple clients, batch scripts |

---

## Pattern 4: Long Polling

The client sends a request, and the server holds it open until the result is ready (or a timeout elapses). Reduces the number of round-trips compared to short polling.

### Flow

```
Client                              Server
  │                                      │
  │── POST /jobs {payload} ──►           │
  │◄── 202 {job_id} ──                  │
  │                                      │
  │── GET /jobs/{job_id}?wait=30 ──►     │
  │    (server holds request for         │
  │     up to 30 seconds)               │
  │                                      │    (worker completes at T+12s)
  │◄── {"status": "succeeded", ...} ──  │    (server responds immediately)
```

### Server Implementation

```python
@app.get("/jobs/{job_id}")
async def get_job(job_id: str, wait: int = 0):
    """
    If wait > 0, hold the request until status changes or timeout.
    """
    status = await get_job_status(job_id)

    if wait > 0 and status["state"] == "pending":
        # Subscribe to updates and wait
        event = asyncio.Event()
        _waiters.setdefault(job_id, []).append(event)

        try:
            await asyncio.wait_for(event.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass
        finally:
            _waiters.get(job_id, []).remove(event)

        status = await get_job_status(job_id)  # Re-fetch after wait

    return status
```

### Characteristics

| Aspect | Detail |
|--------|--------|
| **Latency** | Low — near-instant once result is ready |
| **Server cost** | Medium — held connections, but fewer total requests than short polling |
| **Client complexity** | Low — same as short polling, just with a `wait` parameter |
| **Infrastructure** | Needs server/LB timeouts > wait parameter |
| **Best for** | When you want low latency but can't use WebSocket/SSE |

---

## Pattern 5: Webhook (Client Callback)

The client provides a callback URL when submitting the job. The server POSTs the result to that URL when done.

### Flow

```
Client                              Server                      Worker
  │                                      │                         │
  │── POST /jobs                         │                         │
  │   {payload, callback_url} ──►        │                         │
  │◄── 202 {job_id} ──                   │── dispatch ──►          │
  │                                      │                         │── work
  │                                      │                         │
  │                                      │            ◄── done ────│
  │               ◄── POST callback_url  │                         │
  │                   {job_id, result}   │                         │
  │── 200 OK ──►                         │                         │
```

### Server Implementation

```python
@app.post("/jobs")
async def create_job(payload: dict, callback_url: Optional[str] = None):
    job = await store_job(payload, callback_url=callback_url)
    await dispatch_to_worker(job)
    return {"job_id": job.id}

async def on_worker_complete(job_id: str, result: dict):
    job = await get_job(job_id)

    if job.callback_url:
        async with httpx.AsyncClient() as client:
            for attempt in range(3):  # Retry webhook delivery
                try:
                    resp = await client.post(job.callback_url, json={
                        "job_id": job_id,
                        "status": "succeeded",
                        "result": result,
                    }, timeout=10)
                    if resp.status_code < 300:
                        break
                except httpx.HTTPError:
                    await asyncio.sleep(2 ** attempt)
```

### Characteristics

| Aspect | Detail |
|--------|--------|
| **Latency** | Lowest — push-based, no polling overhead |
| **Server cost** | Lowest — no held connections, no polling load |
| **Client complexity** | Medium — must expose an endpoint, handle retries, verify authenticity |
| **Infrastructure** | Client must be reachable from server (not always possible) |
| **Best for** | Server-to-server, CI/CD pipelines, Slack/GitHub-style integrations |

### Security Considerations

- **HMAC verification**: Sign the callback payload with a shared secret so the client can verify it came from your server.
- **Idempotency**: The client's callback endpoint must be idempotent (same delivery = same effect) because retries will happen.
- **Allowlisting**: Validate callback URLs to prevent SSRF (don't POST to `http://169.254.169.254/`).

---

## Pattern 6: Redis Pub/Sub Notification

The client subscribes to a Redis channel for the job. The orchestrator publishes the result when the worker completes.

### Flow

```
Client                     Redis                     Orchestrator
  │                           │                           │
  │── SUBSCRIBE job:{id} ──►  │                           │
  │   (blocking listen)       │                           │
  │                           │                           │── worker completes
  │                           │  ◄── PUBLISH job:{id}     │
  │                           │      {result}             │
  │◄── message received ──    │                           │
  │── UNSUBSCRIBE ──►         │                           │
```

### Client Implementation (Python)

```python
import redis.asyncio as redis

async def wait_for_result(redis_client: redis.Redis, job_id: str, timeout: float = 600.0):
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"job:{job_id}")

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                return json.loads(message["data"])
    finally:
        await pubsub.unsubscribe(f"job:{job_id}")
```

### With Fallback (Pub/Sub + Polling)

Pub/Sub is fire-and-forget — if the client subscribes *after* the result is published, it misses it. Always combine with a check:

```python
async def wait_for_result_safe(redis_client: redis.Redis, job_id: str):
    # 1. Check if already done (race condition safety)
    existing = await redis_client.get(f"result:{job_id}")
    if existing:
        return json.loads(existing)

    # 2. Subscribe and wait
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"job:{job_id}")

    # 3. Check again after subscribing (close the race window)
    existing = await redis_client.get(f"result:{job_id}")
    if existing:
        await pubsub.unsubscribe(f"job:{job_id}")
        return json.loads(existing)

    # 4. Wait for pub/sub message
    async for message in pubsub.listen():
        if message["type"] == "message":
            await pubsub.unsubscribe(f"job:{job_id}")
            return json.loads(message["data"])
```

### Characteristics

| Aspect | Detail |
|--------|--------|
| **Latency** | Near-instant (pub/sub) |
| **Server cost** | Low — Redis handles the fan-out |
| **Client complexity** | Medium — must handle subscribe/unsubscribe lifecycle, race conditions |
| **Infrastructure** | Requires shared Redis instance accessible to both client and server |
| **Best for** | Internal microservices, when Redis is already in the stack |

---

## Comparison Matrix

| Pattern | Latency | Server Load | Client Complexity | Needs Special Infra | Real-Time Progress | Best For |
|---------|---------|-------------|-------------------|--------------------|--------------------|----------|
| **WebSocket** | Instant | High (connections) | Medium | WS-capable LB | Yes | Interactive UIs |
| **SSE** | Instant | Medium | Low | None | Yes | Progress bars, dashboards |
| **Short Polling** | Medium | Medium (requests) | Lowest | None | Yes (per-poll) | Scripts, simple clients |
| **Long Polling** | Low | Medium (held conns) | Low | Longer LB timeouts | No (one-shot) | Low-latency without WS |
| **Webhook** | Instant | Lowest | Medium | Client must be reachable | No | Server-to-server |
| **Redis Pub/Sub** | Instant | Low | Medium | Shared Redis | No (one-shot) | Internal microservices |

---

## Combining Patterns

**Production best practice:** Offer multiple delivery mechanisms and let the client choose.

```python
@app.post("/jobs")
async def create_job(
    payload: dict,
    callback_url: Optional[str] = None,   # Webhook delivery
    # Client can also:
    # - Connect to /ws/jobs/{id} for WebSocket
    # - GET /jobs/{id}/stream for SSE
    # - GET /jobs/{id} for polling
    # - SUBSCRIBE job:{id} on Redis for pub/sub
):
    ...
```

A typical production API offers:
1. **Short polling** as the universal fallback (always works)
2. **WebSocket or SSE** for interactive clients that need progress
3. **Webhook** for server-to-server integrations
