# HTTPX Advanced Features

> **Topics**: HTTP/2, streaming, retries, DNS, and error handling.

---

## 1. HTTP/1.1 vs HTTP/2

### HTTP/1.1 Behavior

- **One request per connection** at a time
- High concurrency requires many sockets
- Head-of-line blocking

```python
# HTTP/1.1: 10 concurrent requests need 10 connections
client = httpx.AsyncClient()  # HTTP/1.1 by default
```

### HTTP/2 Behavior

- **Multiple requests per connection** (multiplexing)
- Streams share a single TCP connection
- Fewer sockets needed

```python
# HTTP/2: 10 concurrent requests can use 1-2 connections
client = httpx.AsyncClient(http2=True)
```

### Enabling HTTP/2

```python
# Requires h2 package
# pip install httpx[http2]

client = httpx.AsyncClient(http2=True)
```

### When HTTP/2 Helps

| Scenario | HTTP/2 benefit |
|----------|----------------|
| Many requests to same host | Fewer connections |
| High latency networks | Reduced handshakes |
| Server push support | Proactive data |

### When HTTP/2 Doesn't Help

| Scenario | Why not |
|----------|---------|
| Single requests | No multiplexing needed |
| Different hosts | Each host needs its own connection |
| Server doesn't support | Falls back to HTTP/1.1 |

---

## 2. Streaming Responses

### Buffered (Default)

```python
response = await client.get(url)
data = response.json()  # entire body in memory
```

**Problem**: Large responses consume memory.

### Streaming

```python
async with client.stream("GET", url) as response:
    async for chunk in response.aiter_bytes():
        process(chunk)
```

**Benefits**:
- Process data incrementally
- Lower memory footprint
- Works with `read` timeout (per-chunk)

### Streaming JSON Lines

```python
async with client.stream("GET", url) as response:
    async for line in response.aiter_lines():
        record = json.loads(line)
        process(record)
```

### Large File Download

```python
async with client.stream("GET", url) as response:
    async with aiofiles.open("output.bin", "wb") as f:
        async for chunk in response.aiter_bytes(chunk_size=8192):
            await f.write(chunk)
```

---

## 3. DNS Behavior

### How DNS Works in HTTPX

- Uses OS resolver (system DNS)
- Happens during `connect` phase
- **No HTTPX-level DNS caching**

### DNS Impact on Latency

```
Uncached DNS: 10-100ms
Cached DNS:   0-5ms
```

Slow DNS directly increases connection time.

### DNS Failure Modes

| Failure | Effect |
|---------|--------|
| DNS timeout | `ConnectTimeout` |
| DNS NXDOMAIN | `ConnectError` |
| DNS server down | `ConnectTimeout` or `ConnectError` |

### Mitigations

1. **System DNS cache**: Configure OS resolver
2. **Connection reuse**: Avoid DNS by keeping connections open
3. **Adequate connect timeout**: Account for DNS variability

---

## 4. Transport-Level Retries

HTTPX supports transport-level retries:

```python
transport = httpx.AsyncHTTPTransport(retries=2)
client = httpx.AsyncClient(transport=transport)
```

### What Gets Retried

- Connection failures
- Network errors (TCP reset, etc.)

### What Does NOT Get Retried

- HTTP errors (4xx, 5xx)
- Timeouts
- Application errors

### Safe for Idempotent Methods

```
GET, HEAD, OPTIONS, TRACE → safe to retry
POST, PUT, DELETE, PATCH → retry with caution
```

### Recommendation

For most production use, implement retries in application code:

```python
# Application-level retry (more control)
for attempt in range(3):
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code >= 500:
            await asyncio.sleep(backoff(attempt))
            continue
        raise
```

---

## 5. Error Model

HTTPX provides lifecycle-aligned exceptions.

### Exception Hierarchy

```
httpx.HTTPError
├── httpx.RequestError          (network/transport issues)
│   ├── httpx.TransportError
│   │   ├── httpx.TimeoutException
│   │   │   ├── httpx.ConnectTimeout
│   │   │   ├── httpx.PoolTimeout
│   │   │   ├── httpx.WriteTimeout
│   │   │   └── httpx.ReadTimeout
│   │   └── httpx.ConnectError
│   └── httpx.DecodingError
└── httpx.HTTPStatusError       (4xx/5xx responses)
```

### Catching Errors

```python
try:
    response = await client.get(url)
    response.raise_for_status()
except httpx.ConnectTimeout:
    # Network unreachable, DNS slow
    logger.error("Connection timeout")
except httpx.ReadTimeout:
    # Server stopped responding
    logger.error("Read timeout")
except httpx.PoolTimeout:
    # Pool exhausted
    logger.error("Pool timeout - consider increasing max_connections")
except httpx.ConnectError:
    # Connection refused, network down
    logger.error("Connection error")
except httpx.HTTPStatusError as e:
    # 4xx/5xx response
    logger.error(f"HTTP error: {e.response.status_code}")
except httpx.RequestError as e:
    # Other request errors
    logger.error(f"Request error: {e}")
```

### Best Practice

Catch specific exceptions to handle each failure mode appropriately.

---

## 6. Request Context

### Headers

```python
client = httpx.AsyncClient(
    headers={
        "User-Agent": "MyApp/1.0",
        "Authorization": "Bearer token",
    }
)
```

### Base URL

```python
client = httpx.AsyncClient(
    base_url="https://api.example.com/v1"
)

# Now relative paths work
response = await client.get("/users")  # https://api.example.com/v1/users
```

### Request ID Propagation

```python
response = await client.get(
    url,
    headers={"X-Request-ID": request_id}
)
```

---

## 7. Connection Lifecycle Events

### Hooks for Observability

```python
async def log_request(request):
    logger.info(f"Request: {request.method} {request.url}")

async def log_response(response):
    logger.info(f"Response: {response.status_code}")

client = httpx.AsyncClient(
    event_hooks={
        "request": [log_request],
        "response": [log_response],
    }
)
```

### Use Cases

- Request/response logging
- Metrics collection
- Tracing integration

---

## 8. Production Checklist

### Client Configuration

- [ ] Explicit `max_connections` limit
- [ ] Explicit `max_keepalive_connections`
- [ ] All four timeouts configured
- [ ] HTTP/2 enabled if beneficial
- [ ] Base URL set if using single API

### Error Handling

- [ ] Catch specific timeout exceptions
- [ ] Handle `HTTPStatusError` for 4xx/5xx
- [ ] Retry logic for transient errors
- [ ] Log with context (request ID, attempt)

### Lifecycle

- [ ] Create client once at startup
- [ ] Reuse across requests
- [ ] Close properly on shutdown

### Monitoring

- [ ] Track pool wait times
- [ ] Track connection creation rate
- [ ] Alert on timeout errors
- [ ] Monitor error rates by type

---

## 9. Complete Production Client

```python
import httpx
import structlog

logger = structlog.get_logger()


async def create_production_client() -> httpx.AsyncClient:
    """
    Create a production-configured HTTPX client.
    """
    
    async def log_request(request):
        logger.debug(
            "http_request",
            method=request.method,
            url=str(request.url),
        )
    
    async def log_response(response):
        logger.debug(
            "http_response",
            status_code=response.status_code,
            url=str(response.url),
        )
    
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=50,
            max_keepalive_connections=20,
        ),
        timeout=httpx.Timeout(
            connect=5.0,
            pool=5.0,
            write=10.0,
            read=30.0,
        ),
        http2=True,
        event_hooks={
            "request": [log_request],
            "response": [log_response],
        },
    )


# Usage
client = await create_production_client()

try:
    response = await client.get("https://api.example.com/data")
    response.raise_for_status()
    return response.json()
finally:
    await client.aclose()
```

---

## Summary

| Feature | When to use |
|---------|-------------|
| HTTP/2 | Multiple requests to same host |
| Streaming | Large responses, memory constraints |
| Transport retries | Simple network resilience |
| Event hooks | Logging, metrics, tracing |

**Key principles**:

1. HTTP/2 reduces connections but requires server support
2. Streaming enables bounded memory usage
3. Error handling should be specific to failure type
4. Production clients need explicit configuration

---

**Next**: [HTTPX vs aiohttp](05_httpx_vs_aiohttp.md) — library comparison
