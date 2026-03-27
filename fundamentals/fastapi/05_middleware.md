# FastAPI Middleware

A complete guide to middleware in FastAPI -- from mental model to production patterns.

---

## 1. What Is Middleware?

Middleware is a **request/response interceptor**. It sits between the client and your route handlers, processing every request before it reaches an endpoint and every response before it leaves the server.

### The Onion Model

Middleware forms layers around your application, like an onion. Each request passes **inward** through every middleware layer, hits the route handler, then passes **outward** through the same layers in reverse.

```
Request  -->  Middleware A  -->  Middleware B  -->  Route Handler
Response <--  Middleware A  <--  Middleware B  <--  Route Handler
```

Each middleware can:

- Modify the request before passing it inward
- Modify the response before passing it outward
- Short-circuit the chain (return early without calling the route)
- Execute code before **and** after the route handler

### What Middleware Is For

- Cross-cutting concerns that apply to **every** request
- Request/response transformation
- Observability (logging, metrics, tracing)
- Security headers
- Error normalization

### What Middleware Is NOT For

- Per-route logic (use dependencies)
- Authentication checks on specific endpoints (use dependencies)
- Request validation (use Pydantic models)

---

## 2. Two Kinds of Middleware

FastAPI supports two middleware styles. Understanding the difference is critical.

### `@app.middleware("http")` -- The Simple Way

```python
from fastapi import FastAPI, Request

app = FastAPI()

@app.middleware("http")
async def my_middleware(request: Request, call_next):
    # Before route handler
    response = await call_next(request)
    # After route handler
    return response
```

This uses `BaseHTTPMiddleware` internally. FastAPI wraps your function for you.

### ASGI Middleware -- The Standard Way

```python
from starlette.types import ASGIApp, Receive, Scope, Send

class MyMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            # Your logic here
            pass
        await self.app(scope, receive, send)

app.add_middleware(MyMiddleware)
```

This is the raw ASGI protocol. No abstractions, no wrappers.

### Comparison

| Feature | `@app.middleware("http")` | Pure ASGI Middleware |
|---------|--------------------------|----------------------|
| Complexity | Low | High |
| Access to `Request`/`Response` objects | Yes (built-in) | Manual (must construct) |
| Performance | Slower (reads entire body into memory) | Faster (streams bytes) |
| WebSocket support | No | Yes |
| Streaming response support | Problematic (buffers body) | Full support |
| Use case | Simple request/response logging | High-performance, streaming, WebSockets |

### The Recommendation

> **Use pure ASGI middleware for production.** `@app.middleware("http")` and `BaseHTTPMiddleware` are convenient for learning and prototyping, but they have fundamental architectural problems that make them unsuitable for production services. See Section 9 for the full explanation.

**Use `@app.middleware("http")` / `BaseHTTPMiddleware` only when:**

- You are prototyping or learning
- You are certain you will never stream responses
- You accept the performance overhead

**Use pure ASGI middleware (recommended) when:**

- You are writing production code
- You handle streaming responses (SSE, file downloads)
- You use WebSockets
- You care about performance and memory usage
- You need correct client disconnect handling

---

## 3. Request ID Middleware

Every production service needs request tracing. Inject a unique ID into every request and propagate it to logs, downstream services, and the response.

> **Note**: The examples in Sections 3-5 use `BaseHTTPMiddleware` for clarity. For production, use the pure ASGI versions shown in Section 12.

### Implementation

```python
import uuid
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Use incoming header if present (from API gateway / load balancer)
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # Store in request state for access in route handlers
        request.state.request_id = request_id

        response = await call_next(request)

        # Echo back in response headers
        response.headers["X-Request-ID"] = request_id
        return response

app = FastAPI()
app.add_middleware(RequestIDMiddleware)
```

### Accessing in Route Handlers

```python
@app.get("/orders")
async def list_orders(request: Request):
    request_id = request.state.request_id
    logger.info("Listing orders", extra={"request_id": request_id})
    return {"orders": []}
```

### Propagating to Structured Logging

For production, use contextvars so **every** log line in the request automatically includes the request ID -- no manual passing required.

```python
import contextvars
import logging
import uuid
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)

class RequestIDFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_ctx.get("")
        return True

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_ctx.reset(token)

# Configure logging
handler = logging.StreamHandler()
handler.addFilter(RequestIDFilter())
formatter = logging.Formatter(
    "%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s"
)
handler.setFormatter(formatter)
logging.root.addHandler(handler)
```

Now every log line produced during a request includes the request ID automatically:

```
2024-03-15 10:22:01 [a1b2c3d4-...] INFO app: Listing orders
2024-03-15 10:22:01 [a1b2c3d4-...] INFO db: Executing query SELECT ...
```

---

## 4. Timing Middleware

Measure and expose request duration for every endpoint.

```python
import time
import logging
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "Request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )

        # Expose as response header for client-side observability
        response.headers["X-Process-Time-Ms"] = str(round(duration_ms, 2))
        return response

app = FastAPI()
app.add_middleware(TimingMiddleware)
```

### Why `time.perf_counter()` and Not `time.time()`

| Function | Monotonic | Resolution | Use Case |
|----------|-----------|------------|----------|
| `time.time()` | No (affected by NTP/clock adjustments) | Microseconds | Wall clock / timestamps |
| `time.perf_counter()` | Yes | Nanoseconds | Measuring durations |

Always use `perf_counter()` for duration measurement. `time.time()` can go backward if the system clock is adjusted.

### Production Enhancement: Prometheus Metrics

For real production services, emit histograms instead of (or in addition to) logs:

```python
from prometheus_client import Histogram

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Request duration in seconds",
    labelnames=["method", "path", "status"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

class PrometheusTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        REQUEST_DURATION.labels(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        ).observe(duration)

        return response
```

---

## 5. Error Handling Middleware

Catch unhandled exceptions and return consistent, safe error responses. Never leak stack traces to clients.

### The Problem

Without error handling middleware:

```python
# Unhandled exception -> Uvicorn returns plain text "Internal Server Error"
# Different format than your normal JSON responses
# Stack trace might leak in debug mode
```

Clients get inconsistent error shapes. Some errors are JSON, some are plain text.

### The Solution

```python
import logging
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            # Log the full traceback -- this stays server-side
            logger.error(
                "Unhandled exception",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "request_id": getattr(request.state, "request_id", "unknown"),
                    "traceback": traceback.format_exc(),
                },
            )

            # Return a safe, consistent error response
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_server_error",
                    "message": "An unexpected error occurred.",
                    "request_id": getattr(
                        request.state, "request_id", "unknown"
                    ),
                },
            )

app = FastAPI()
app.add_middleware(ErrorHandlingMiddleware)
```

### What Clients See

Every error response has the same shape:

```json
{
    "error": "internal_server_error",
    "message": "An unexpected error occurred.",
    "request_id": "a1b2c3d4-e5f6-..."
}
```

### Anti-Patterns

```python
# ❌ Leaking implementation details to clients
except Exception as exc:
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "traceback": traceback.format_exc()},
    )

# ❌ Catching too broadly and swallowing errors
except Exception:
    return JSONResponse(status_code=500, content={"error": "error"})
    # No logging! You will never know what went wrong.

# ❌ Re-raising after sending a response
except Exception as exc:
    response = JSONResponse(status_code=500, content={"error": "failed"})
    raise exc  # Response already sent, this causes a secondary error
```

```python
# ✅ Log everything server-side, return safe message to client
except Exception as exc:
    logger.error("Unhandled exception", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred.",
        },
    )
```

---

## 6. CORS Middleware

Cross-Origin Resource Sharing controls which frontend origins can call your API.

### Basic Configuration

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com", "https://admin.example.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
    max_age=600,  # Cache preflight responses for 10 minutes
)
```

### Parameters Explained

| Parameter | Purpose | Default |
|-----------|---------|---------|
| `allow_origins` | Which origins can make requests | `[]` |
| `allow_credentials` | Allow cookies/auth headers | `False` |
| `allow_methods` | Allowed HTTP methods | `["GET"]` |
| `allow_headers` | Allowed request headers | `[]` |
| `expose_headers` | Headers the browser can read from the response | `[]` |
| `max_age` | Seconds the browser caches preflight responses | `600` |

### Common Mistakes

```python
# ❌ Using wildcard with credentials -- browsers REJECT this
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,  # Cannot combine * origins with credentials
)

# ❌ Forgetting to expose custom headers -- browser cannot read them
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],
    # Missing: expose_headers=["X-Request-ID"]
    # Frontend JS cannot access X-Request-ID from response
)

# ❌ Overly permissive for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    # Fine for development, dangerous for production
)
```

```python
# ✅ Explicit origins, methods, and headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.example.com",
        "https://staging.example.com",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-ID"],
)
```

### Environment-Aware Configuration

```python
import os

ALLOWED_ORIGINS = {
    "production": [
        "https://app.example.com",
    ],
    "staging": [
        "https://staging.example.com",
        "https://preview.example.com",
    ],
    "development": [
        "http://localhost:3000",
        "http://localhost:5173",
    ],
}

env = os.getenv("ENVIRONMENT", "development")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS.get(env, []),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

---

## 7. Trusted Host Middleware

Prevents **host header attacks** by rejecting requests with unexpected `Host` headers. This protects against DNS rebinding, cache poisoning, and password reset link hijacking.

```python
from starlette.middleware.trustedhost import TrustedHostMiddleware

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["api.example.com", "*.example.com"],
)
```

### How It Works

1. Reads the `Host` header from every incoming request
2. Compares against `allowed_hosts`
3. Returns `400 Bad Request` if the host is not allowed

### When to Use

| Scenario | Needed? |
|----------|---------|
| Public API behind a load balancer | Yes -- LB may pass through the original Host |
| Internal microservice | Depends on network trust model |
| Behind a reverse proxy that sets Host | Yes -- defense in depth |
| Local development | No |

```python
# ❌ Allowing all hosts defeats the purpose
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

# ✅ Explicit allowed hosts
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["api.example.com", "api-internal.example.com"],
)
```

---

## 8. GZip Middleware

Compresses responses to reduce bandwidth. The client must send `Accept-Encoding: gzip`.

```python
from starlette.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)
```

### Parameters

| Parameter | Purpose | Default |
|-----------|---------|---------|
| `minimum_size` | Minimum response size (bytes) before compression kicks in | `500` |

### When to Use

| Scenario | Recommendation |
|----------|----------------|
| JSON API responses > 1KB | Use GZip |
| Small responses (< 500 bytes) | Skip -- compression overhead not worth it |
| Binary / already compressed (images, video) | Skip -- already compressed |
| Behind a CDN/reverse proxy that compresses | Skip -- let the proxy handle it |
| High-throughput, CPU-constrained service | Be cautious -- compression uses CPU |

### Important

GZip middleware compresses the response in the application process. In most production setups, **the reverse proxy (Nginx, Caddy, CloudFlare) handles compression**, making application-level GZip unnecessary and wasteful.

```python
# ❌ Double compression -- proxy compresses already-compressed response
# Nginx gzip on + GZipMiddleware = wasted CPU, no benefit

# ✅ Let the reverse proxy handle compression
# Only use GZipMiddleware if your app serves responses directly to clients
```

---

## 9. Custom Middleware Class (ASGI)

### Using BaseHTTPMiddleware

The easiest way to write a class-based middleware:

```python
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

class MyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, custom_value: str = "default"):
        super().__init__(app)
        self.custom_value = custom_value

    async def dispatch(self, request: Request, call_next):
        # Pre-processing
        request.state.custom = self.custom_value

        response = await call_next(request)

        # Post-processing
        response.headers["X-Custom"] = self.custom_value
        return response

app.add_middleware(MyMiddleware, custom_value="production")
```

### Why You Should Avoid BaseHTTPMiddleware in Production

`BaseHTTPMiddleware` has fundamental architectural problems, not just edge-case limitations. Understanding **how** it works makes the problems obvious.

#### How `call_next()` Actually Works

When you call `await call_next(request)`, BaseHTTPMiddleware:

1. **Spawns a new `asyncio.Task`** to run the rest of the middleware chain + your endpoint
2. The endpoint writes its response into an **in-memory byte buffer** (not to the client)
3. Only after the endpoint **finishes completely** does `call_next()` return
4. The entire response body is now **held in memory**
5. Your middleware reads the buffered response and sends it to the client

```
BaseHTTPMiddleware flow:
  request → dispatch() → call_next() → [spawns Task] → endpoint runs
                                        → body buffered in memory
                                        → Task completes
                         ← Response (full body in memory)
  ← sends buffered body to client

Pure ASGI flow:
  request → __call__() → app(scope, receive, send_wrapper)
                          → endpoint runs
                          → each chunk streams directly to client via send_wrapper
  (no buffering, no extra Task)
```

#### The Concrete Problems

1. **Response body buffered in memory.** A 500MB file download? The entire file sits in RAM before a single byte reaches the client. SSE streams? The connection hangs until the stream "ends" (which for SSE may be never).

2. **Extra `asyncio.Task` per request.** Every request creates an additional Task. Under high load, this means thousands of extra Tasks competing for the event loop — measurable overhead.

3. **Broken streaming.** `StreamingResponse` and SSE rely on sending chunks incrementally. BaseHTTPMiddleware collects all chunks into a buffer first, then sends them all at once. Your "streaming" endpoint becomes a regular endpoint that blocks until all data is generated.

4. **Client disconnect not propagated.** When a client disconnects mid-request, the spawned Task doesn't know. Your endpoint keeps running, burning CPU and I/O on a response nobody will receive. In pure ASGI, the `receive` callable signals disconnect immediately.

5. **WebSocket incompatible.** BaseHTTPMiddleware only handles `http` scope. WebSocket connections bypass it entirely, meaning your logging/timing/error-handling middleware doesn't apply to WebSocket endpoints.

#### The Performance Impact

Benchmarks consistently show **15-25% higher latency** and **significantly higher memory usage** with BaseHTTPMiddleware compared to pure ASGI, even for simple middleware that just adds a header. The overhead comes from Task creation, response buffering, and the extra copy of the response body.

### Pure ASGI Middleware

For full control, write directly against the ASGI spec:

```python
import time
import logging
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

class PureASGITimingMiddleware:
    """
    Measures request duration without buffering the response body.
    Works with streaming responses and WebSockets.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            # Pass through non-HTTP connections (WebSocket, lifespan)
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500  # Default if no response is sent

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # Inject header into the response
                headers = list(message.get("headers", []))
                duration_ms = (time.perf_counter() - start) * 1000
                headers.append(
                    (b"x-process-time-ms", str(round(duration_ms, 2)).encode())
                )
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            request = Request(scope)
            logger.info(
                "Request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )

app.add_middleware(PureASGITimingMiddleware)
```

### BaseHTTPMiddleware vs Pure ASGI

| Aspect | BaseHTTPMiddleware | Pure ASGI |
|--------|-------------------|-----------|
| Ease of writing | Simple -- `dispatch(request, call_next)` | More code -- raw `scope`/`receive`/`send` |
| Response body | Buffered entirely in memory | Streamed chunk-by-chunk |
| WebSocket support | No | Yes |
| Streaming support | No (silently breaks streaming) | Full support |
| Client disconnect | Not propagated to endpoint | Propagated immediately |
| Memory usage | Entire response body in RAM | Constant (streaming) |
| Performance overhead | ~15-25% higher latency | Baseline |
| Extra Tasks per request | 1 additional `asyncio.Task` | None |
| When to use | Prototyping, learning | Production |

> **Use pure ASGI for production middleware.** The code is more verbose, but you write middleware once and it runs on every request for the life of the service. The investment in learning the ASGI interface pays for itself immediately. The examples in Section 12 show production-ready pure ASGI versions of every common middleware.

---

## 10. Middleware Ordering

### Why Order Matters

Middleware is executed in **reverse registration order** for requests and **registration order** for responses. The **last** middleware added with `add_middleware()` is the **outermost** layer.

```python
app = FastAPI()

app.add_middleware(MiddlewareA)  # Added first  -> innermost
app.add_middleware(MiddlewareB)  # Added second -> outermost
```

Execution order:

```
Request  -->  B  -->  A  -->  Route Handler
Response <--  B  <--  A  <--  Route Handler
```

This is because FastAPI/Starlette wraps the app: `B(A(app))`. The outer wrapper executes first.

### Recommended Order

Register middleware from **innermost to outermost** (most specific first, most general last):

```python
app = FastAPI()

# 1. Innermost -- app-specific logic
app.add_middleware(ErrorHandlingMiddleware)

# 2. Observability -- timing and tracing
app.add_middleware(TimingMiddleware)

# 3. Request context -- request ID for logs
app.add_middleware(RequestIDMiddleware)

# 4. Security -- CORS, trusted hosts
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# 5. Outermost -- infrastructure concerns
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["api.example.com"])
app.add_middleware(GZipMiddleware, minimum_size=1000)
```

This produces the execution order:

```
Request  --> GZip --> TrustedHost --> CORS --> RequestID --> Timing --> ErrorHandler --> Route
Response <-- GZip <-- TrustedHost <-- CORS <-- RequestID <-- Timing <-- ErrorHandler <-- Route
```

### Why This Order

| Middleware | Why This Position |
|------------|-------------------|
| GZip (outermost) | Compresses the final response after all headers are set |
| TrustedHost | Rejects bad hosts early, before any processing |
| CORS | Handles preflight OPTIONS requests early |
| RequestID | Available to all inner middleware and route handlers |
| Timing | Measures duration of actual request processing, not infra overhead |
| ErrorHandler (innermost) | Catches exceptions from route handlers |

### Common Pitfalls

```python
# ❌ Error handler outside timing -- unhandled exceptions bypass timing
app.add_middleware(TimingMiddleware)        # Inner
app.add_middleware(ErrorHandlingMiddleware) # Outer -- catches errors before timing logs them

# ❌ Request ID inside timing -- timing logs have no request ID
app.add_middleware(RequestIDMiddleware)  # Inner
app.add_middleware(TimingMiddleware)     # Outer -- request ID not set yet when timing logs

# ❌ CORS inside error handler -- CORS headers missing on error responses
app.add_middleware(CORSMiddleware, ...)     # Inner
app.add_middleware(ErrorHandlingMiddleware) # Outer -- error responses skip CORS headers
```

---

## 11. Common Mistakes

### Mistake 1: Blocking the Event Loop

```python
# ❌ Synchronous I/O in async middleware -- blocks ALL concurrent requests
class BadMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        import requests  # sync HTTP library
        result = requests.get("https://auth.example.com/verify")  # BLOCKS
        response = await call_next(request)
        return response
```

```python
# ✅ Use async I/O or run in a thread pool
import httpx
from asyncio import to_thread

class GoodMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Option A: Async HTTP client
        async with httpx.AsyncClient() as client:
            result = await client.get("https://auth.example.com/verify")

        # Option B: Run sync code in thread pool (last resort)
        # result = await to_thread(sync_verify_function)

        response = await call_next(request)
        return response
```

### Mistake 2: Modifying Response After It Is Sent

```python
# ❌ Trying to modify status code after call_next
class BadMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.status_code = 200  # May not work -- headers already sent
        return response
```

With `BaseHTTPMiddleware`, this may appear to work because the body is buffered. With pure ASGI, the status code is sent in `http.response.start` before the body -- modifying it afterward has no effect.

```python
# ✅ If you need to transform responses, do it before sending
# Or use pure ASGI middleware and intercept http.response.start
```

### Mistake 3: Creating Resources Per Request in Middleware

```python
# ❌ Creating an HTTP client on every request
class BadMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        client = httpx.AsyncClient()  # New client every request!
        request.state.client = client
        response = await call_next(request)
        await client.aclose()
        return response
```

```python
# ✅ Use lifespan to create shared resources
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    app.state.client = httpx.AsyncClient()
    yield
    await app.state.client.aclose()

app = FastAPI(lifespan=lifespan)

class GoodMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Access the shared client
        client = request.app.state.client
        response = await call_next(request)
        return response
```

### Mistake 4: Middleware vs Dependencies -- Using the Wrong One

This is the most common architectural mistake. Middleware and dependencies serve different purposes.

| Concern | Middleware | Dependency |
|---------|-----------|------------|
| Scope | Every request | Specific endpoints/routers |
| Access to route info | No (runs before routing) | Yes (knows which endpoint) |
| Can inject into endpoint | No (only `request.state`) | Yes (`Depends()`) |
| Can short-circuit | Yes (return response early) | Yes (raise `HTTPException`) |
| Testability | Harder (integration test) | Easy (`dependency_overrides`) |
| Use for | Global, cross-cutting | Per-route, business logic |

```python
# ❌ Using middleware for per-route authentication
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/admin"):
            token = request.headers.get("Authorization")
            if not verify_token(token):
                return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        return await call_next(request)
# Problems: route matching logic duplicated, hard to test, no access to route metadata
```

```python
# ✅ Using dependencies for per-route authentication
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def require_admin(token: str = Depends(oauth2_scheme)):
    user = await verify_token(token)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

@app.get("/admin/users", dependencies=[Depends(require_admin)])
async def list_admin_users():
    ...
```

### Mistake 5: Heavy Logic in Middleware

```python
# ❌ Database queries in middleware for every request
class BadMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # This runs on EVERY request, including health checks, static files, etc.
        user = await db.query("SELECT * FROM users WHERE token = ?", request.headers.get("auth"))
        request.state.user = user
        return await call_next(request)
```

```python
# ✅ Keep middleware lightweight -- defer heavy work to dependencies
class GoodMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Lightweight: just extract and attach the token
        request.state.auth_token = request.headers.get("Authorization")
        return await call_next(request)

# Heavy work happens only on routes that need it
async def get_current_user(request: Request):
    token = request.state.auth_token
    if not token:
        raise HTTPException(status_code=401)
    return await verify_and_load_user(token)
```

---

## 12. Putting It All Together

A production middleware stack using **pure ASGI middleware**. No BaseHTTPMiddleware — streaming works, WebSockets work, and there is no extra Task overhead per request.

```python
import json
import logging
import time
import uuid
import contextvars
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


# --- Pure ASGI Middleware Classes ---


class RequestIDMiddleware:
    """Injects a request ID into every request and echoes it in the response."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extract or generate request ID
        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())

        # Store in scope state so route handlers can access it
        scope.setdefault("state", {})["request_id"] = request_id

        # Bind to contextvars for automatic log propagation
        token = request_id_ctx.set(request_id)

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_ctx.reset(token)


class TimingMiddleware:
    """Measures request duration, logs it, and adds an X-Process-Time-Ms header."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500

        async def send_with_timing(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                duration_ms = (time.perf_counter() - start) * 1000
                headers.append(
                    (b"x-process-time-ms", str(round(duration_ms, 2)).encode())
                )
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Request completed",
                extra={
                    "request_id": request_id_ctx.get(""),
                    "method": scope.get("method", ""),
                    "path": scope.get("path", ""),
                    "status": status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )


class ErrorHandlingMiddleware:
    """Catches unhandled exceptions, logs them, returns consistent JSON error."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            request_id = request_id_ctx.get("unknown")
            logger.error(
                "Unhandled exception",
                exc_info=True,
                extra={
                    "request_id": request_id,
                    "method": scope.get("method", ""),
                    "path": scope.get("path", ""),
                },
            )

            # Only send error response if headers haven't been sent yet
            if not response_started:
                body = json.dumps({
                    "error": "internal_server_error",
                    "message": "An unexpected error occurred.",
                    "request_id": request_id,
                }).encode()

                await send({
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": body,
                })


# --- Application Setup ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=30.0)
    yield
    await app.state.http_client.aclose()


app = FastAPI(lifespan=lifespan)

# Register middleware: innermost first, outermost last
app.add_middleware(ErrorHandlingMiddleware)     # Innermost: catches route exceptions
app.add_middleware(TimingMiddleware)            # Measures processing time
app.add_middleware(RequestIDMiddleware)         # Sets request ID for all inner middleware
app.add_middleware(                             # Handles CORS preflight
    CORSMiddleware,
    allow_origins=["https://app.example.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
)
app.add_middleware(                             # Rejects bad hosts
    TrustedHostMiddleware,
    allowed_hosts=["api.example.com"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)  # Outermost: compresses final response
```

### Why This Is Better

Compared to the BaseHTTPMiddleware version:

| Concern | BaseHTTPMiddleware | This Pure ASGI Stack |
|---------|-------------------|---------------------|
| Streaming | Broken (body buffered) | Works (chunks flow through) |
| WebSockets | Skipped entirely | Handled (request ID works on WS too) |
| Memory | Entire response in RAM | Constant (pass-through) |
| Tasks per request | 3 extra (one per middleware) | 0 extra |
| Client disconnect | Not propagated | Propagated immediately |
| Error after headers sent | Crashes | Handled gracefully (`response_started` check) |

---

## Quick Reference

| Middleware | Purpose | Built-in? |
|-----------|---------|-----------|
| `CORSMiddleware` | Cross-origin request handling | Yes (Starlette) |
| `TrustedHostMiddleware` | Host header validation | Yes (Starlette) |
| `GZipMiddleware` | Response compression | Yes (Starlette) |
| `HTTPSRedirectMiddleware` | Redirect HTTP to HTTPS | Yes (Starlette) |
| Request ID | Trace requests across logs/services | Write your own |
| Timing | Measure request duration | Write your own |
| Error handling | Consistent error responses | Write your own |

---

## Key Takeaways

1. **Middleware is for cross-cutting concerns** -- things that apply to every request. Use dependencies for per-route logic.
2. **Order matters** -- register innermost first, outermost last. The last `add_middleware` call is the outermost layer.
3. **Use pure ASGI middleware in production** -- `BaseHTTPMiddleware` buffers entire response bodies in memory, breaks streaming, ignores WebSockets, spawns extra Tasks per request, and doesn't propagate client disconnects. It is fine for prototyping but should not be in your production stack.
4. **Never block the event loop** -- all I/O in middleware must be async.
5. **Keep middleware lightweight** -- it runs on every request, including health checks and preflight requests.
6. **Use contextvars for request-scoped data** -- this propagates context to all code in the request without manual passing.
