# HTTPX vs aiohttp

> **Core difference**: aiohttp is lower-level and more flexible. HTTPX is higher-level with safer defaults.

---

## 1. Philosophy

| Aspect | HTTPX | aiohttp |
|--------|-------|---------|
| Design | High-level, requests-like API | Low-level, flexible |
| Defaults | Safe, opinionated | Minimal, configurable |
| Target user | Application developers | System/infrastructure developers |

---

## 2. Timeout Models

### HTTPX: Phase-Based

```python
timeout = httpx.Timeout(
    connect=5.0,    # DNS + TCP + TLS
    pool=5.0,       # wait for socket
    write=10.0,     # send request
    read=30.0       # receive response
)
```

**Characteristics**:
- Clear mapping to network lifecycle
- `read` timeout resets per chunk (safe for streaming)
- Harder to misuse
- Explicit about what each timeout controls

### aiohttp: Total + Socket

```python
timeout = aiohttp.ClientTimeout(
    total=60,        # total request time
    connect=5,       # connection establishment
    sock_read=30,    # socket read
    sock_connect=5   # socket connect
)
```

**Characteristics**:
- `total` is a global ceiling
- Easier to accidentally break streaming with `total`
- More tuning required for correct behavior
- `sock_read` does not reset per chunk by default

### Practical Difference

```python
# HTTPX: This works fine with streaming
timeout = httpx.Timeout(read=30.0)
# read=30s means "30s between chunks"

# aiohttp: This can break streaming
timeout = aiohttp.ClientTimeout(total=60)
# total=60s means "entire request must complete in 60s"
```

---

## 3. Connection Pooling

### HTTPX: Explicit Pool Behavior

```python
limits = httpx.Limits(
    max_connections=100,        # total sockets
    max_keepalive_connections=20  # idle sockets
)
```

**Characteristics**:
- Pool queue exposed via `pool` timeout
- Clear separation: pool limits vs task limits
- Easy to understand contention behavior

### aiohttp: Connector-Based

```python
connector = aiohttp.TCPConnector(
    limit=100,           # total connections
    limit_per_host=0,    # per-host limit (0 = unlimited)
    ttl_dns_cache=300    # DNS caching built-in
)
session = aiohttp.ClientSession(connector=connector)
```

**Characteristics**:
- More implicit behavior
- Built-in DNS caching (HTTPX uses OS resolver)
- Easier to confuse connector limits with application concurrency
- `limit_per_host` provides per-host control HTTPX lacks

---

## 4. HTTP/2 Support

### HTTPX

```python
client = httpx.AsyncClient(http2=True)
```

- First-class HTTP/2 support
- Easy to enable
- Works transparently

### aiohttp

- Limited HTTP/2 support
- Not a primary feature
- May require additional packages

**Winner**: HTTPX for HTTP/2 use cases.

---

## 5. Streaming and Low-Level Control

### aiohttp Excels At

| Use case | Why aiohttp |
|----------|-------------|
| Long-lived connections | Fine-grained socket control |
| WebSockets | Native, mature support |
| Proxies/gateways | Lower-level access |
| Custom protocols | More extensibility |

### HTTPX Suffices For

| Use case | Why HTTPX |
|----------|-----------|
| API clients | Clean, simple API |
| Standard HTTP | Safe defaults |
| Most production services | Guardrails prevent mistakes |

---

## 6. Error Model

### HTTPX: Lifecycle-Specific

```python
httpx.ConnectTimeout    # DNS + TCP + TLS failed
httpx.PoolTimeout       # no socket available
httpx.ReadTimeout       # response stalled
httpx.WriteTimeout      # request stalled
```

Each exception maps to a specific phase.

### aiohttp: Broader Categories

```python
aiohttp.ServerTimeoutError    # general timeout
aiohttp.ClientConnectorError  # connection issues
asyncio.TimeoutError          # often bubbles up
```

Less granular, harder to handle specific failures.

---

## 7. API Design

### HTTPX

```python
async with httpx.AsyncClient() as client:
    response = await client.get(url)
    data = response.json()
```

- Familiar to `requests` users
- Sync and async APIs mirror each other
- Response methods are synchronous

### aiohttp

```python
async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
        data = await response.json()
```

- Double context manager pattern
- Response methods are async
- More explicit about resource lifecycle

---

## 8. Feature Comparison

| Feature | HTTPX | aiohttp |
|---------|-------|---------|
| Sync API | ✅ | ❌ |
| HTTP/2 | ✅ Native | ⚠️ Limited |
| WebSockets | ❌ | ✅ Native |
| DNS caching | ❌ (OS) | ✅ Built-in |
| Per-host limits | ❌ | ✅ |
| Phase-based timeouts | ✅ | ⚠️ Partial |
| requests-like API | ✅ | ⚠️ |
| Connection hooks | ✅ | ✅ |
| Proxy support | ✅ | ✅ |
| Streaming | ✅ | ✅ |

---

## 9. When to Choose What

### Choose HTTPX If

- Building an API client
- Want predictable behavior under load
- Need HTTP/2
- Want guardrails against common mistakes
- Team has varying experience levels
- Standard REST/JSON APIs

### Choose aiohttp If

- Building a proxy or gateway
- Need deep socket control
- Handling long-lived connections
- WebSocket-heavy workloads
- Need per-host connection limits
- Accept higher complexity for more power

---

## 10. Migration Considerations

### From aiohttp to HTTPX

```python
# aiohttp
async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
        data = await response.json()

# HTTPX equivalent
async with httpx.AsyncClient() as client:
    response = await client.get(url)
    data = response.json()
```

**Watch out for**:
- `response.json()` is sync in HTTPX
- Timeout model is different
- No built-in DNS caching

### From HTTPX to aiohttp

```python
# HTTPX
async with httpx.AsyncClient() as client:
    response = await client.get(url)
    data = response.json()

# aiohttp equivalent
async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
        data = await response.json()
```

**Watch out for**:
- Response must be consumed in context manager
- `await response.json()` (async)
- Configure `total` timeout carefully

---

## 11. Performance

### Benchmark Considerations

| Factor | HTTPX | aiohttp |
|--------|-------|---------|
| Raw throughput | Comparable | Comparable |
| Connection reuse | Good | Good |
| Memory overhead | Slightly higher | Lower |
| HTTP/2 multiplexing | Reduces connections | N/A |

**Reality**: For most applications, the difference is negligible. Choose based on features and ergonomics, not benchmarks.

---

## 12. Final Rule of Thumb

> **aiohttp gives you power.**
> **HTTPX gives you guardrails.**

For most API client use cases, HTTPX's safer defaults and cleaner API make it the better choice.

For infrastructure, proxies, WebSockets, or when you need fine-grained control, aiohttp's flexibility is worth the complexity.

---

## Summary

| Aspect | HTTPX | aiohttp |
|--------|-------|---------|
| Best for | API clients | Infrastructure |
| Learning curve | Lower | Higher |
| Defaults | Safe | Minimal |
| Control | Medium | High |
| HTTP/2 | Native | Limited |
| WebSockets | No | Yes |
| Footguns | Few | More |

Choose the tool that matches your use case and team expertise.
