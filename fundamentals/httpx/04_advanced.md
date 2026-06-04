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
| Repeated/large headers | HPACK header compression |

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


def create_production_client() -> httpx.AsyncClient:
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


# Usage — create the client ONCE and reuse it across requests.
# Creating a client per call throws away the connection pool and pays a
# fresh DNS + TCP + TLS handshake every time. In FastAPI, build it in a
# lifespan handler and close it on shutdown.

client = create_production_client()  # long-lived, app-scoped


async def fetch_data():
    response = await client.get("https://api.example.com/data")
    response.raise_for_status()
    return response.json()


async def shutdown():
    await client.aclose()
```

---

## TLS / SSL Configuration

HTTPX uses the system's default CA bundle (via `certifi`) and verifies certificates by default. Most of the time you don't have to touch it. The times you do:

### Custom CA bundle

Internal services that use a private CA (self-signed, corporate root, Let's Encrypt staging) aren't in `certifi`'s default bundle.

As of httpx 0.28, passing a path string to `verify=` is deprecated (it raises a warning). Build an `ssl.SSLContext` and pass that instead:

```python
import ssl
import httpx

ctx = ssl.create_default_context()
ctx.load_verify_locations(cafile="/path/to/ca-bundle.pem")
# Or a directory of PEM files (OpenSSL-style):
# ctx.load_verify_locations(capath="/etc/ssl/custom-cas/")

client = httpx.AsyncClient(verify=ctx)
```

Environment override without changing code: `SSL_CERT_FILE=/path/to/bundle.pem` (read by OpenSSL/`certifi`).

### Disabling verification — only for debugging

```python
client = httpx.AsyncClient(verify=False)   # NEVER in production
```

This disables certificate verification entirely — you're vulnerable to MITM. If the problem is "my internal CA isn't trusted," fix it with the custom CA bundle above; don't reach for `verify=False`.

### Mutual TLS (client certificates)

When the server authenticates the client via cert (common in internal / zero-trust setups). As of httpx 0.28 the `cert=` argument is deprecated; load the client cert into an `ssl.SSLContext` via `load_cert_chain` instead:

```python
import ssl
import httpx

ctx = ssl.create_default_context()
ctx.load_cert_chain(certfile="/path/to/client.crt", keyfile="/path/to/client.key")

# A single PEM file containing both cert and key:
# ctx.load_cert_chain(certfile="/path/to/client-combined.pem")

# Password-protected key:
# ctx.load_cert_chain(
#     certfile="/path/to/client.crt",
#     keyfile="/path/to/client.key",
#     password="keypassword",
# )

client = httpx.AsyncClient(verify=ctx)
```

### Pinning / custom SSL context

The `ssl.SSLContext` approach above is also how you get full control (cipher suites, TLS version floor, alternative trust stores). Passing an `SSLContext` via `verify=` is the supported path in httpx 0.28+:

```python
import ssl

ctx = ssl.create_default_context(cafile="/path/to/ca-bundle.pem")
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
ctx.set_ciphers("ECDHE+AESGCM:!aNULL")

client = httpx.AsyncClient(verify=ctx)
```

### Common pitfalls

- **Certificate errors in Docker** — some base images ship an outdated CA bundle. The `certifi` package that HTTPX uses is usually fine; if not, `apt-get install ca-certificates` in the image.
- **Connecting to an IP instead of a hostname** — certs are issued for hostnames. Use the hostname and set up DNS; avoid `verify=False` as a workaround.
- **Clock skew** — a large clock drift on your host can fail cert validation even when the cert is valid. Check NTP sync if validation fails in weird ways.
- **HTTP/2 over TLS** — `http2=True` requires ALPN negotiation to pick h2; that only works over TLS (HTTPS), not plain HTTP.

---

## Summary

| Feature | When to use |
|---------|-------------|
| HTTP/2 | Multiple requests to same host |
| Streaming | Large responses, memory constraints |
| Transport retries | Simple network resilience |
| Event hooks | Logging, metrics, tracing |
| Custom CA / mTLS | Internal services, zero-trust networks |

**Key principles**:

1. HTTP/2 reduces connections but requires server support
2. Streaming enables bounded memory usage
3. Error handling should be specific to failure type
4. Production clients need explicit configuration
5. Never disable TLS verification in production — fix the trust chain instead

---

**Next**: [HTTPX vs aiohttp](05_httpx_vs_aiohttp.md) — library comparison
