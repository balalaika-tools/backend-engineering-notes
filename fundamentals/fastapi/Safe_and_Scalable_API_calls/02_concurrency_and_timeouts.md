# Part 2: Concurrency and Timeouts

> **Principle**: Client timeouts are the only true request kill-switch. `asyncio.timeout` is advisory.

---

## 1. The Timeout Hierarchy

There are **multiple timeout layers**, each serving a different purpose:

```
┌─────────────────────────────────────────┐
│         SLA Boundary Timeout            │ ← Total request budget
│  ┌───────────────────────────────────┐  │
│  │       Queue Timeout               │  │ ← Admission control
│  │  ┌─────────────────────────────┐  │  │
│  │  │     Call Timeout            │  │  │ ← Per-attempt budget
│  │  │  ┌───────────────────────┐  │  │  │
│  │  │  │   Client Timeouts     │  │  │  │ ← Socket-level control
│  │  │  │ (connect/read/write)  │  │  │  │
│  │  │  └───────────────────────┘  │  │  │
│  │  └─────────────────────────────┘  │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

Each layer has a **specific responsibility**.

---

## 2. `asyncio.timeout` Is NOT a Real Request Timeout

`asyncio.timeout`:
- **Cancels the Python task**
- Does **NOT** immediately terminate sockets
- Does **NOT** guarantee immediate resource release
- Does **NOT** stop vendor-side processing

It provides **application-level cancellation**, not transport-level enforcement.

```python
async with asyncio.timeout(30):
    response = await client.get(url)
```

When this timeout fires:
1. The coroutine receives `CancelledError`
2. The socket **may still be open** briefly
3. The vendor **may still be processing**
4. Resources are **eventually** cleaned up

---

## 3. Client Timeouts Are the Only True Kill-Switch

Only **client-level timeouts** (e.g., `httpx.Timeout`) reliably terminate requests at the socket level.

```python
timeout = httpx.Timeout(
    connect=5.0,   # DNS + TCP + TLS
    pool=5.0,      # wait for socket
    write=10.0,    # send request
    read=30.0      # receive response
)
```

These timeouts:
- Act **directly** on TCP/TLS I/O
- **Bound kernel resource usage**
- Are **required** to avoid silent resource leaks

**Rule**: Application timeouts should be slightly larger than client timeouts.

---

## 4. Timeouts Are Not Guillotines

Timeout expiration:
- Requests cancellation
- Does **NOT** guarantee immediate socket closure
- May leave sockets open briefly

This allows **temporary concurrency overshoot** unless constrained elsewhere.

```
Timeout fires → cancel signal sent → cleanup begins → socket closes
                ↑                                      ↑
            instant                              may take time
```

This is why client limits are mandatory — they prevent unbounded overshoot.

---

## 5. Queue Timeout (Admission Control)

```python
async with asyncio.timeout(5):
    async with llm_rate:
        async with llm_sem:
            ...
```

**Purpose**: "If I can't even START the call within 5 seconds, give up."

**Covers**:
- Waiting on the rate limiter
- Waiting on the semaphore

**Protects against**:
- Unbounded queues
- Request pile-up
- Latency explosion under overload

**Behavior**: Raises `asyncio.TimeoutError`, should **not** be retried.

---

## 6. Call Timeout (Per-Attempt)

```python
async with asyncio.timeout(30):
    response = await client.post(url, json=payload)
```

**Purpose**: "Once the vendor call starts, I'll wait at most 30 seconds."

**Covers**:
- TCP connection establishment (if not reused)
- Request transmission
- Vendor processing time
- Response reading

**Protects against**:
- Zombie calls
- Unbounded latency
- Resource leaks

**Behavior**: May be retried if transient.

---

## 7. SLA Boundary Timeout

```python
@app.post("/llm")
async def llm_endpoint(payload: dict):
    async with asyncio.timeout(60):  # SLA boundary
        return await call_llm(payload)
```

**Purpose**: "This entire endpoint must complete within 60 seconds."

**Covers**:
- Everything: queue time, retries, vendor latency

**Protects**:
- User-facing latency guarantees
- SLA compliance
- System predictability

**Behavior**: Hard failure, return HTTP 504.

---

## 8. Client Timeout Components

### `connect` Timeout

Time allowed for connection establishment:
- DNS lookup
- TCP handshake
- TLS handshake

Does **not** include HTTP data exchange.

**Typical value**: 5 seconds

### `pool` Timeout

Time allowed to acquire a socket from the pool.

Only applies when `max_connections` is reached.

**Typical value**: 5 seconds

### `write` Timeout

Time allowed to send request data:
- HTTP headers
- Request body

Relevant for uploads or slow networks.

**Typical value**: 5-10 seconds

### `read` Timeout

Time allowed to receive response data.

**Critical**: Resets when data is received (per-chunk, not total).

Safe for streaming responses.

**Typical value**: 30-60 seconds for LLMs

---

## 9. Timeout Configuration Example

```python
# Client timeouts (transport level)
client = httpx.AsyncClient(
    timeout=httpx.Timeout(
        connect=5.0,
        pool=5.0,
        write=10.0,
        read=30.0,
    ),
    limits=httpx.Limits(
        max_connections=50,
        max_keepalive_connections=20,
    ),
)


async def call_llm(payload: dict):
    # Queue timeout (admission control)
    async with asyncio.timeout(5):
        async with llm_rate:
            async with llm_sem:
                
                # Call timeout (per-attempt)
                async with asyncio.timeout(30):
                    response = await client.post(url, json=payload)
                    return response.json()


@app.post("/llm")
async def llm_endpoint(payload: dict):
    # SLA boundary (total request budget)
    async with asyncio.timeout(60):
        return await call_llm(payload)
```

---

## 10. Exception Handling: httpx vs asyncio Timeouts

### Important Distinction

`httpx` does **NOT** raise `asyncio.TimeoutError`.

It raises:
- `httpx.ConnectTimeout` — connection failed
- `httpx.ReadTimeout` — response stalled
- `httpx.WriteTimeout` — request stalled
- `httpx.PoolTimeout` — no socket available

All inherit from `httpx.TimeoutException`.

### Different Meanings

| Error type | Meaning | Retry? |
|------------|---------|--------|
| `httpx.TimeoutException` | Vendor / network slowness | ✅ Usually yes |
| `asyncio.TimeoutError` | Your system's budget expired | ⚠️ Context-dependent |

### Handling Both

```python
try:
    async with asyncio.timeout(30):
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()

except httpx.TimeoutException as e:
    # Transport timeout — vendor/network issue
    # Consider retry
    logger.warning("vendor_timeout", error=str(e))
    raise RetryableError("Vendor timeout")

except asyncio.TimeoutError:
    # Application timeout — could be queue or call
    # Be careful about retry
    logger.warning("application_timeout")
    raise
```

---

## 11. Timeout Relationships

### Size Relationships

```
connect < pool < write < read
     5s     5s     10s    30s

call_timeout > max(client timeouts)
       30s   >    30s (read)

sla_timeout > call_timeout × max_retries
      60s   >    30s × 2 (with backoff)
```

### Why Order Matters

- `connect` should fail fast — network unreachable
- `pool` should match admission expectations
- `read` should accommodate vendor latency variance
- `call_timeout` should cap per-attempt cost
- `sla_timeout` should bound total user wait

---

## 12. Common Timeout Mistakes

### ❌ Same timeout everywhere

```python
# WRONG
client = httpx.AsyncClient(timeout=30.0)
```

**Problem**: Connect should fail fast (5s), read may need longer (30s).

**Fix**: Configure each phase explicitly.

### ❌ No application timeout

```python
# WRONG
response = await client.post(url)  # relies only on client timeout
```

**Problem**: No SLA boundary, no queue protection.

**Fix**: Wrap with `asyncio.timeout`.

### ❌ Call timeout inside client timeout

```python
# WRONG
client = httpx.AsyncClient(timeout=30.0)
async with asyncio.timeout(30):  # same as client
    await client.post(...)
```

**Problem**: Application timeout fires at same time as client timeout.

**Fix**: Application timeout should be slightly larger or serve different purpose.

### ❌ Queue timeout too large

```python
# WRONG
async with asyncio.timeout(30):  # wait 30s just to start
    async with sem:
        ...
```

**Problem**: User waits 30s before any work starts.

**Fix**: Queue timeout should be small (2-5s).

---

## 13. Practical Timeout Values

### LLM / AI APIs

```python
httpx.Timeout(
    connect=5.0,
    pool=5.0,
    write=10.0,
    read=60.0,      # LLMs can be slow
)

queue_timeout = 5    # fail fast if overloaded
call_timeout = 60    # match read timeout
sla_timeout = 90     # allow 1 retry + overhead
```

### Fast REST APIs

```python
httpx.Timeout(
    connect=3.0,
    pool=3.0,
    write=5.0,
    read=10.0,
)

queue_timeout = 3
call_timeout = 10
sla_timeout = 20
```

### File Upload APIs

```python
httpx.Timeout(
    connect=5.0,
    pool=10.0,
    write=120.0,    # large uploads
    read=30.0,
)

queue_timeout = 10
call_timeout = 120
sla_timeout = 180
```

---

## 14. Summary: Timeout Purposes

| Timeout | Layer | Purpose | Typical |
|---------|-------|---------|---------|
| `connect` | Client | Network reachability | 5s |
| `pool` | Client | Socket availability | 5s |
| `write` | Client | Request sending | 5-10s |
| `read` | Client | Response receiving | 10-60s |
| Queue | Application | Admission control | 2-5s |
| Call | Application | Per-attempt budget | 30-60s |
| SLA | Application | Total request budget | 60-120s |

---

## Key Principles

1. **Client timeouts control sockets** — `asyncio.timeout` does not
2. **Queue timeout protects admission** — fail fast under overload
3. **Call timeout bounds attempts** — prevent zombie calls
4. **SLA timeout bounds requests** — user-facing guarantee
5. **Different exceptions for different layers** — handle appropriately

---

**Next**: [Part 3: Call Patterns](03_call_patterns.md) — gold standard implementation and retry logic.
