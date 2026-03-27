# HTTPX Connection Pooling

> **Core idea**: Pool limits control how many sockets exist, not how many coroutines run.

---

## 1. Pool Limits Configuration

```python
limits = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=20
)

client = httpx.AsyncClient(limits=limits)
```

---

## 2. `max_connections`

**What it is**: Maximum number of TCP connections that may exist at the same time.

```python
httpx.Limits(max_connections=100)
```

### What This Controls

- Total sockets across all hosts
- Includes **active + idle** sockets
- Hard ceiling on network concurrency

### When Limit Is Reached

If you have 100 active connections and request #101 arrives:

```python
await client.get(...)  # waits in pool queue
```

The request **waits** (does not fail immediately) until:
1. A connection becomes free
2. `pool` timeout expires

### What This Does NOT Control

- Number of async tasks
- Application-level concurrency
- Memory usage from pending requests

---

## 3. `max_keepalive_connections`

**What it is**: Maximum number of **idle** sockets kept open for reuse.

```python
httpx.Limits(max_keepalive_connections=20)
```

### What This Controls

- How many sockets stay open after requests complete
- Upper bound on "warm" connections

### When This Matters

After a burst of traffic:

```
Peak: 100 active connections
After burst: 20 kept idle, 80 closed
```

This prevents:
- Wasting server-side resources during low traffic
- Accumulating stale connections
- File descriptor exhaustion

### Relationship to `max_connections`

```
max_keepalive_connections ≤ max_connections
```

- `max_connections`: ceiling during load
- `max_keepalive_connections`: floor during idle

---

## 4. Pool Behavior Summary

| Situation | Pool behavior |
|-----------|---------------|
| Request arrives, idle socket exists | Reuse socket immediately |
| Request arrives, no idle socket, under limit | Create new socket |
| Request arrives, at `max_connections` | Wait in pool queue |
| Request completes, under keepalive limit | Return socket to pool |
| Request completes, at keepalive limit | Close socket |

---

## 5. Pool Queue and `pool` Timeout

When all connections are busy:

```python
# All 100 connections in use
await client.get(...)  # enters pool queue
```

The `pool` timeout controls how long this wait is allowed:

```python
timeout = httpx.Timeout(pool=5.0)
```

### What Happens

| Outcome | Result |
|---------|--------|
| Socket becomes free within 5s | Request proceeds |
| 5s passes, no socket free | `httpx.PoolTimeout` raised |

### Common Mistake

```python
# WRONG: No pool timeout
timeout = httpx.Timeout(connect=5.0, read=30.0)
# pool defaults to 5.0, but explicit is better
```

Always set `pool` timeout explicitly in production.

---

## 6. Per-Host Limits

By default, HTTPX does **not** limit connections per host.

```python
# All 100 connections could go to one host
limits = httpx.Limits(max_connections=100)
```

### Implication

If you call multiple APIs:

```python
await asyncio.gather(
    client.get("https://api1.example.com/..."),  # could use 50
    client.get("https://api2.example.com/..."),  # could use 50
)
```

One slow API could starve connections for another.

### Solution

For multi-API scenarios, consider:
1. Separate clients per API
2. Application-level semaphores per API

---

## 7. Connection Reuse Mechanics

### Keep-Alive Protocol

HTTP/1.1 uses `Connection: keep-alive` header (default).

```
Client → Server: GET /data HTTP/1.1
                 Connection: keep-alive

Server → Client: HTTP/1.1 200 OK
                 Connection: keep-alive
```

Both sides agree to keep the socket open.

### When Connections Close

| Reason | Who closes |
|--------|-----------|
| `Connection: close` header | Either side |
| Server idle timeout | Server |
| Server max requests reached | Server |
| Pool limit reached | Client |
| Client closed | Client |

### Detection

When server closes connection:
- HTTPX detects on next use
- Automatically creates new connection
- Transparent to your code

---

## 8. Production Configuration

### Small Service (Low Traffic)

```python
limits = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10
)
```

### Medium Service

```python
limits = httpx.Limits(
    max_connections=50,
    max_keepalive_connections=20
)
```

### High-Traffic Service

```python
limits = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=30
)
```

### Considerations

| Factor | Effect on limits |
|--------|------------------|
| Target latency distribution | Higher variance → more connections |
| Upstream API capacity | Match their limits |
| Pod resources | Memory for buffers, file descriptors |
| Concurrent users | Scale with expected concurrency |

---

## 9. Monitoring Pool Health

### Key Metrics

Track these in production:

| Metric | What it indicates |
|--------|-------------------|
| Pool wait time | Contention for connections |
| Active connections | Current concurrency |
| Idle connections | Warm connection availability |
| Connection creation rate | Handshake frequency |
| Pool timeout errors | Undersized pool |

### Warning Signs

- High pool wait times → pool too small
- Many new connections → poor reuse or aggressive closing
- Pool timeout errors → increase `max_connections` or reduce concurrency

---

## 10. Common Mistakes

### ❌ Pool Too Small

```python
limits = httpx.Limits(max_connections=5)
# With 50 concurrent requests → massive pool queue
```

**Fix**: Size pool for expected concurrency.

### ❌ Pool Too Large

```python
limits = httpx.Limits(max_connections=1000)
# Opens 1000 connections to vendor → they rate limit you
```

**Fix**: Match pool size to what downstream can handle.

### ❌ Keepalive Too High

```python
limits = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=100
)
# Keeps all connections open forever
```

**Fix**: Keepalive should be a fraction of max.

### ❌ No Pool Timeout

```python
client = httpx.AsyncClient()  # pool timeout = 5.0 (default)
```

**Fix**: Explicitly configure all timeouts.

---

## Summary

| Setting | Purpose | Typical range |
|---------|---------|---------------|
| `max_connections` | Peak concurrent sockets | 20-100 |
| `max_keepalive_connections` | Idle sockets retained | 10-30 |
| `pool` timeout | Max wait for socket | 3-10s |

**Key principle**:

> Pool limits bound **network concurrency** at the socket level.
> They do not limit application concurrency.

---

**Next**: [Timeouts](03_timeouts.md) — phase-based timeout configuration
