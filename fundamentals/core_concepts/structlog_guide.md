# Structured Logging with structlog

> **Who this is for**: Python backend developers who understand stdlib
> [logging](logging/README.md) and need searchable event fields, request-scoped
> context, consistent third-party logs, and a production-safe processor pipeline.

`structlog` builds log events as dictionaries and transforms them through a
processor chain. It can render directly or integrate with stdlib logging for
handlers and third-party libraries. Production readiness comes from the complete
configuration and operating model—not from installing the package alone.

---

## 1. Why Structured Logging

### The Problem with Unstructured Logs

stdlib logging produces human-readable strings:

```
2025-07-05 15:00:00 [INFO] payment.service: Payment processed for user 42, amount $99.50, order #1081
```

This works when you're reading logs in a terminal. It fails when you need to:

- **Search** for all payments over $50
- **Alert** on error rates per user
- **Correlate** a request across 5 microservices
- **Aggregate** metrics from log data

You'd need regex to extract `user=42` from a free-text string. Every format change breaks your parsers.

### Structured Logging Fixes This

Structured logging emits **key-value pairs**, typically as JSON:

```json
{"event": "payment_processed", "user_id": 42, "amount": 99.50, "order_id": 1081, "timestamp": "2025-07-05T15:00:00Z", "level": "info"}
```

Now every log aggregator (Datadog, ELK, CloudWatch, Loki) can:

- Index fields automatically
- Filter by `user_id=42` without regex
- Build dashboards from log fields
- Correlate across services by `request_id`

### stdlib logging vs structlog

| Aspect | stdlib `logging` | `structlog` |
|--------|-----------------|-------------|
| Output format | String templates (`%(message)s`) | Event dictionaries rendered however you configure |
| Adding context | Pass `extra={}` dict, easy to forget | `.bind()` attaches context that persists |
| Processor pipeline | Handlers + Formatters (class-based) | Chain of simple functions |
| Bound context | `LoggerAdapter` or `extra={}` | `.bind()` returns a logger with copied local context |
| Performance | Good | Good (pluggable fast JSON via `orjson`/`msgspec`) |
| stdlib compatibility | Native | Full integration -- can wrap or replace |

> **Key insight**: structlog can render directly, but most backend applications
> integrate it with stdlib logging so framework and dependency logs use the same
> handlers and output format.

---

## 2. Setup

### Install

```bash
pip install structlog
```

### Minimal Configuration

```python
# app/logging_config.py
import logging
import structlog

def setup_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),  # pretty output for development
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

Call this **once** at application startup:

```python
# main.py
from app.logging_config import setup_logging

setup_logging()
```

### The Processor Chain -- Mental Model

Every log event flows through a **pipeline of processors**, each transforming the event dict:

```
log.info("request_handled", status=200)
    |
    v
[merge_contextvars]  --> adds request_id, user_id from context
    |
    v
[add_log_level]      --> adds {"level": "info"}
    |
    v
[TimeStamper]        --> adds {"timestamp": "2025-07-05T15:00:00Z"}
    |
    v
[ConsoleRenderer]    --> formats for terminal (dev) or JSONRenderer (prod)
    |
    v
OUTPUT
```

Each processor is a function with this signature:

```python
def my_processor(logger, method_name, event_dict):
    # modify event_dict
    event_dict["custom_field"] = "value"
    return event_dict
```

That's it. No classes to inherit. No XML config. Just functions that transform a dict.

---

## 3. Bound Loggers

### Getting a Logger

```python
import structlog

log = structlog.get_logger()
```

This gives you a **bound logger** -- the core abstraction in structlog.

### Basic Usage

```python
log.info("server_started", port=8000, workers=4)
log.warning("cache_miss", key="user:42")
log.error("payment_failed", user_id=42, reason="insufficient_funds")
```

Output (dev console):

```
2025-07-05 15:00:00 [info     ] server_started                 port=8000 workers=4
2025-07-05 15:00:01 [warning  ] cache_miss                     key=user:42
2025-07-05 15:00:02 [error    ] payment_failed                 reason=insufficient_funds user_id=42
```

Output (production JSON):

```json
{"event": "server_started", "port": 8000, "workers": 4, "level": "info", "timestamp": "2025-07-05T15:00:00Z"}
```

### `.bind()` -- Persistent Context

`.bind()` returns a **new logger** with additional context attached to every subsequent log call:

```python
log = structlog.get_logger()

# Create a logger bound to this user's session
user_log = log.bind(user_id=42, plan="premium")

user_log.info("page_viewed", page="/dashboard")
# {"event": "page_viewed", "page": "/dashboard", "user_id": 42, "plan": "premium"}

user_log.info("settings_changed", theme="dark")
# {"event": "settings_changed", "theme": "dark", "user_id": 42, "plan": "premium"}
```

`user_id` and `plan` are included automatically. No need to pass them to every call.

### `.unbind()` -- Remove Context

```python
clean_log = user_log.unbind("plan")
clean_log.info("logged_out")
# {"event": "logged_out", "user_id": 42}  <-- no "plan"
```

### `.new()` -- Reset Context

```python
fresh_log = user_log.new(request_id="abc-123")
fresh_log.info("fresh_start")
# {"event": "fresh_start", "request_id": "abc-123"}  <-- user_id is gone
```

### Why Immutability Matters

`.bind()` never mutates the original logger. It returns a new one.

```python
base = structlog.get_logger()
log_a = base.bind(service="auth")
log_b = base.bind(service="billing")

log_a.info("check")  # service=auth
log_b.info("check")  # service=billing
base.info("check")   # no service field
```

This is safe for concurrent use. No shared mutable state.

---

## 4. Processors

### Built-in Processors You Should Know

| Processor | What It Does | When to Use |
|-----------|-------------|-------------|
| `add_log_level` | Adds `"level": "info"` to event dict | Always |
| `TimeStamper(fmt="iso")` | Adds ISO 8601 timestamp | Always |
| `StackInfoRenderer()` | Renders stack info if `stack_info=True` | Always |
| `format_exc_info` | Formats exception tracebacks | Always (production) |
| `UnicodeDecoder()` | Ensures all strings are unicode | If you handle bytes |
| `JSONRenderer()` | Serializes event dict to JSON string | Production |
| `dev.ConsoleRenderer()` | Pretty-prints with colors | Development |
| `merge_contextvars` | Merges thread-local/async context | FastAPI / async code |
| `dev.set_exc_info` | Auto-adds exc_info for error/critical | Development |
| `CallsiteParameterAdder()` | Adds filename, function, line number | Debugging |

### Custom Processors

A processor is just a function. This minimal example redacts known top-level
fields:

```python
SENSITIVE_KEYS = {"password", "token", "secret", "authorization", "credit_card"}

def drop_sensitive_fields(logger, method_name, event_dict):
    for key in SENSITIVE_KEYS:
        if key in event_dict:
            event_dict[key] = "[REDACTED]"
    return event_dict
```

Add it to the chain:

```python
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        drop_sensitive_fields,                      # <-- your custom processor
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    # ...
)
```

Now any log call with a sensitive key gets redacted automatically:

```python
log.info("user_login", username="alice", password="hunter2")
# {"event": "user_login", "username": "alice", "password": "[REDACTED]"}
```

This denylist is defense in depth, not a security boundary. It misses nested
values, alternate spellings, credentials embedded in URLs, and secrets inside
free-text exception messages. Prefer an allowlist of fields at each log call,
never log raw headers or payloads, and apply centralized scrubbing before
long-term storage.

### Processor Order Matters

Processors run **top to bottom**. The renderer must be **last**. Filtering/enrichment must come **before** rendering.

```
# Correct order:
1. merge_contextvars       # gather context
2. add_log_level           # enrich
3. drop_sensitive_fields   # filter
4. TimeStamper             # enrich
5. JSONRenderer            # render (LAST)
```

---

## 5. Integration with stdlib logging

This is where most confusion happens. There are two integration directions:

### Direction 1: structlog wraps stdlib (Recommended)

structlog processes your log events, then hands them to stdlib for output. You get structlog's API and processor chain, plus stdlib's handlers (file rotation, syslog, etc.).

```python
import logging
import structlog

def setup_logging():
    # Configure stdlib -- this handles the actual output
    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
    )

    # Configure structlog to use stdlib as its output
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            # Prepare event dict for stdlib
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
```

### Direction 2: stdlib logs go through structlog processors

Third-party libraries use stdlib logging. You want their logs to also be structured JSON. Use `ProcessorFormatter`:

```python
import logging
import structlog

formatter = structlog.stdlib.ProcessorFormatter(
    processors=[
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

handler = logging.StreamHandler()
handler.setFormatter(formatter)

root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)
```

Now when uvicorn or SQLAlchemy logs with stdlib, the output is structured JSON.

### Full Unified Setup (Production Pattern)

This is the setup that handles **both** directions -- your code uses structlog, third-party code uses stdlib, and everything outputs consistent JSON:

```python
import logging
import structlog


def setup_logging(json_logs: bool = True, log_level: str = "INFO") -> None:
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_logs:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    formatter = structlog.stdlib.ProcessorFormatter(
        # Only foreign stdlib records have LogRecord.extra fields to merge.
        foreign_pre_chain=shared_processors + [structlog.stdlib.ExtraAdder()],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    # Configure root logger -- catches all stdlib logging (uvicorn, sqlalchemy, etc.)
    root_logger = logging.getLogger()
    for existing_handler in root_logger.handlers[:]:
        root_logger.removeHandler(existing_handler)
        existing_handler.close()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Configure structlog
    structlog.configure(
        processors=shared_processors + [
            # Preserve logging-style calls such as log.info("user %s", user_id).
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
```

> **Why `shared_processors`?** Structlog events run through this list before
> `wrap_for_formatter`; foreign stdlib records run through the same list as
> `foreign_pre_chain`. `ProcessorFormatter` then removes its private metadata and
> renders both sources identically. Keep exception formatting before the renderer
> so one JSON event contains the traceback instead of emitting a second
> non-JSON traceback block.

This setup intentionally takes ownership of root logging and closes handlers it
replaces. When Uvicorn or another host must retain its handlers, supply one
coordinated logging configuration to the host instead of clearing them after
startup.

---

## 6. FastAPI Integration

### Request-Scoped Context with contextvars

structlog integrates with Python's `contextvars` to provide **request-scoped** context that works across async code without any manual passing:

```python
import structlog

# This clears/binds context for the current async task
structlog.contextvars.clear_contextvars()
structlog.contextvars.bind_contextvars(request_id="abc-123", user_id=42)

# Any logger in this async context sees the bound values
log = structlog.get_logger()
log.info("processing")
# {"event": "processing", "request_id": "abc-123", "user_id": 42}
```

### Request Logging Middleware

This is the production pattern. A middleware that automatically adds context to every log within a request. The middleware mechanics (setting a contextvar on entry, resetting on exit, propagating through async code) are the same as any request-id middleware — see [ContextVars - Pattern 1: Request ID Middleware](../concurrency/async/03_contextvars.md#pattern-1-request-id-middleware-fastapi) for the general form. The structlog-specific part below is how `bind_contextvars` puts the same values into every subsequent `structlog.get_logger().info(...)` call without any manual passing.

```python
import re
import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestLoggingMiddleware:
    """Pure ASGI middleware that owns request log context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.log = structlog.get_logger()

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        candidate = Headers(scope=scope).get("x-request-id")
        request_id = (
            candidate
            if candidate is not None and REQUEST_ID.fullmatch(candidate)
            else uuid.uuid4().hex
        )

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=scope["method"],
            path=scope["path"],
        )

        started = time.perf_counter()
        status_code: int | None = None

        async def send_with_context(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1_000, 2)
            self.log.exception(
                "request_failed",
                status_code=status_code,
                duration_ms=duration_ms,
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - started) * 1_000, 2)
            self.log.info(
                "request_completed",
                status_code=status_code,
                duration_ms=duration_ms,
            )
        finally:
            # Remove request fields even if the app raises or is cancelled.
            structlog.contextvars.clear_contextvars()
```

Register it:

```python
from fastapi import FastAPI
from app.logging_config import setup_logging
from app.middleware import RequestLoggingMiddleware

setup_logging(json_logs=True)

app = FastAPI()
app.add_middleware(RequestLoggingMiddleware)
```

Now every log call inside any endpoint, dependency, or service automatically includes `request_id`, `method`, and `path`:

```python
from fastapi import FastAPI, Depends
import structlog

log = structlog.get_logger()

@app.get("/orders/{order_id}")
async def get_order(order_id: int):
    log.info("fetching_order", order_id=order_id)
    # {"event": "fetching_order", "order_id": 123, "request_id": "abc-123", "method": "GET", "path": "/orders/123"}

    order = await fetch_order(order_id)
    return order
```

You never pass `request_id` manually. The middleware bound it to the context, and `merge_contextvars` in your processor chain picks it up.

The inbound ID is treated as untrusted input: the example accepts only a small,
log-safe character set and bounded length, otherwise it generates a new ID.
Production gateways may instead map an external trace header to a separately
generated internal request ID.

`contextvars` storage is isolated across async tasks and threads. In hybrid
Starlette/FastAPI code, values bound inside a synchronous dependency running in a
thread pool do not automatically flow back into the async task. Bind common
request context in async middleware; pass values explicitly across boundaries
that need to return new context.

### Adding User Context via Dependency

```python
from fastapi import Depends, Request
import structlog

async def bind_user_context(request: Request):
    """Dependency that adds user info to log context after auth."""
    user = getattr(request.state, "user", None)
    if user:
        structlog.contextvars.bind_contextvars(
            user_id=user.id,
            user_role=user.role,
        )

@app.get("/dashboard", dependencies=[Depends(bind_user_context)])
async def dashboard():
    log = structlog.get_logger()
    log.info("dashboard_loaded")
    # {"event": "dashboard_loaded", "user_id": 42, "user_role": "admin", "request_id": "abc-123", ...}
    return {"status": "ok"}
```

---

## 7. Common Patterns

### Correlation IDs Across Services

When service A calls service B, propagate the request ID:

```python
import httpx
import structlog

log = structlog.get_logger()


async def call_billing_service(
    client: httpx.AsyncClient,
    user_id: int,
    amount: float,
) -> dict[str, object]:
    # Get the current request_id from context
    ctx = structlog.contextvars.get_contextvars()
    request_id = ctx.get("request_id", "unknown")

    log.info("calling_billing", user_id=user_id, amount=amount)

    response = await client.post(
        "https://billing.internal/charge",
        json={"user_id": user_id, "amount": amount},
        headers={"X-Request-ID": str(request_id)},
    )
    response.raise_for_status()

    log.info("billing_response", status_code=response.status_code)
    return response.json()
```

Service B's middleware picks up `X-Request-ID` from the header and binds it. Now you can trace a single user action across both services by searching for one request ID.

The caller injects an application-lifetime `AsyncClient` so requests reuse its
connection pool. In systems using distributed tracing, propagate the standard
trace context and log trace/span IDs; a request ID is useful but is not a
replacement for tracing.

### Structured Exception Logging

```python
log = structlog.get_logger()

# ❌ Unstructured -- hard to search, no fields to filter on
try:
    result = process_payment(user_id=42, amount=99.50)
except PaymentError as e:
    log.error(f"Payment failed for user 42: {e}")

# ✅ Structured -- every field is searchable
try:
    result = process_payment(user_id=42, amount=99.50)
except PaymentError as e:
    log.exception(
        "payment_failed",
        user_id=42,
        amount=99.50,
        error_type=type(e).__name__,
        error_detail=str(e),
    )
```

The structured version lets you query: "show me all `payment_failed` events where `amount > 50` in the last hour."

### Performance Logging

```python
import time
import structlog
from contextlib import contextmanager

log = structlog.get_logger()

@contextmanager
def log_duration(operation: str, **extra):
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log.info(
            "operation_completed",
            operation=operation,
            duration_ms=duration_ms,
            **extra,
        )

# Usage
async def get_order(order_id: int):
    with log_duration("db_query", table="orders", order_id=order_id):
        order = await db.fetch_order(order_id)

    with log_duration("serialization", order_id=order_id):
        result = serialize(order)

    return result
```

Output:

```json
{"event": "operation_completed", "operation": "db_query", "table": "orders", "order_id": 123, "duration_ms": 45.2, "request_id": "abc-123"}
{"event": "operation_completed", "operation": "serialization", "order_id": 123, "duration_ms": 1.3, "request_id": "abc-123"}
```

Now you can build dashboards showing p50/p95/p99 for each operation.

Use logs for diagnostic exemplars and request context; use a metrics or tracing
system for reliable latency histograms. Deriving percentiles from sampled,
dropped, or delayed logs can produce biased results and high ingestion cost.

### Log Sampling for High-Throughput Endpoints

```python
import random

def sample_processor(logger, method_name, event_dict):
    """Drop debug logs for health checks to reduce noise."""
    if event_dict.get("path") == "/health" and event_dict.get("level") == "info":
        if random.random() > 0.01:  # log only 1% of health checks
            raise structlog.DropEvent
    return event_dict
```

Place sampling after fields such as `level` and `path` have been added but before
the renderer. Never sample errors, security audit events, billing events, or
other records whose absence changes correctness. Count dropped events with a
metric so sampling itself remains observable.

---

## 8. Development vs Production Config

### The Two-Config Pattern

```python
import logging
import os
import structlog


def setup_logging():
    environment = os.getenv("ENVIRONMENT", "development")
    log_level = os.getenv("LOG_LEVEL", "INFO")
    json_logs = environment != "development"

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_logs:
        # Production: machine-readable JSON, no colors
        renderer = structlog.processors.JSONRenderer()
    else:
        # Development: human-readable, colored output
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors + [structlog.stdlib.ExtraAdder()],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    for existing_handler in root_logger.handlers[:]:
        root_logger.removeHandler(existing_handler)
        existing_handler.close()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
```

### What Each Environment Looks Like

**Development** (`ConsoleRenderer`):

```
2025-07-05 15:00:00 [info     ] request_completed              duration_ms=45.2 method=GET path=/orders/123 request_id=abc-123 status_code=200
```

Colored, aligned, easy to scan by eye.

**Production** (`JSONRenderer`):

```json
{"event": "request_completed", "duration_ms": 45.2, "method": "GET", "path": "/orders/123", "request_id": "abc-123", "status_code": 200, "level": "info", "timestamp": "2025-07-05T15:00:00Z"}
```

One JSON object per line. Machine-parseable. Ready for Datadog, ELK, CloudWatch.

### Comparison

| Aspect | Development | Production |
|--------|------------|------------|
| Renderer | `ConsoleRenderer(colors=True)` | `JSONRenderer()` |
| Format | Aligned, colored text | Single-line JSON |
| Audience | Developer terminal | Log aggregator |
| Log level | `DEBUG` | `INFO` or `WARNING` |
| Performance | Optimize for readability | Measure volume/latency; keep rendering off hot paths when needed |

---

## 9. Common Mistakes

### Mistake 1: Calling `structlog.configure()` in every module

```python
# ❌ WRONG -- reconfiguring in each module
# app/services/payment.py
import structlog
structlog.configure(processors=[...])  # NO!
log = structlog.get_logger()
```

```python
# ✅ CORRECT -- configure once at startup, use everywhere
# app/main.py
from app.logging_config import setup_logging
setup_logging()

# app/services/payment.py
import structlog
log = structlog.get_logger()  # just use it
```

`structlog.configure()` is global. Calling it multiple times overwrites the previous config. Call it **once** at application startup.

### Mistake 2: String formatting instead of key-value pairs

```python
# ❌ WRONG -- log message is an unstructured string
log.info(f"User {user_id} purchased {item_name} for ${amount}")

# ✅ CORRECT -- every value is a searchable field
log.info("purchase_completed", user_id=user_id, item=item_name, amount=amount)
```

The whole point of structured logging is to have fields. If you interpolate everything into the message string, you're back to regex parsing.

### Mistake 3: Forgetting `merge_contextvars` in the processor chain

```python
# ❌ Missing merge_contextvars -- bound context is silently lost
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

structlog.contextvars.bind_contextvars(request_id="abc-123")
log.info("test")
# {"event": "test", "level": "info", "timestamp": "..."}
# Where is request_id? Gone.
```

```python
# ✅ Include merge_contextvars FIRST in the chain
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,  # <-- this must be here
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
```

### Mistake 4: Not using `.bind()` when you have repeated context

```python
# ❌ Repeating the same fields in every call
log.info("order_created", order_id=order_id, user_id=user_id)
log.info("payment_started", order_id=order_id, user_id=user_id)
log.info("email_sent", order_id=order_id, user_id=user_id)

# ✅ Bind once, log many times
order_log = log.bind(order_id=order_id, user_id=user_id)
order_log.info("order_created")
order_log.info("payment_started")
order_log.info("email_sent")
```

### Mistake 5: Using `print()` alongside structlog

```python
# ❌ This bypasses the entire processor chain
print(f"DEBUG: order_id={order_id}")

# ✅ Use the logger -- it goes through processors, gets timestamped, appears in your aggregator
log.debug("debug_checkpoint", order_id=order_id)
```

### Mistake 6: Logging sensitive data

```python
# ❌ PII in logs -- compliance violation
log.info("user_authenticated", email=user.email, password=password)

# ✅ Use a redaction processor (see Section 4) or simply don't log it
log.info("user_authenticated", user_id=user.id)
```

---

## 10. Test Event Fields, Not Rendered Strings

`capture_logs()` makes it easy to assert the event dictionary:

```python
import structlog
from structlog.testing import capture_logs


def test_purchase_log_has_searchable_fields() -> None:
    with capture_logs() as events:
        structlog.get_logger().info(
            "purchase_completed",
            user_id=42,
            amount=99.50,
        )

    assert events == [
        {
            "event": "purchase_completed",
            "log_level": "info",
            "user_id": 42,
            "amount": 99.50,
        }
    ]
```

By default, `capture_logs()` disables configured processors. Pass processors such
as `merge_contextvars` explicitly when their output is part of the contract.
Because capture temporarily changes global structlog configuration, cached
loggers may not observe it; disable `cache_logger_on_first_use` in tests or use a
dedicated `LogCapture` fixture.

Keep separate integration tests for the complete JSON pipeline. Parse each
output line as JSON and assert required fields, one-line exception rendering, and
consistent stdlib/structlog output.

---

## 11. Quick Reference

### Startup Checklist

1. Install: `pip install structlog`
2. Create `logging_config.py` with `setup_logging()`
3. Call `setup_logging()` once in `main.py` before anything else
4. Add `RequestLoggingMiddleware` to FastAPI
5. Use `structlog.get_logger()` in every module
6. Use `ENVIRONMENT` env var to switch between dev/prod rendering

### Processor Chain Template

For direct structlog rendering without `ProcessorFormatter`:

```python
processors = [
    structlog.contextvars.merge_contextvars,     # 1. gather async context
    structlog.stdlib.add_log_level,              # 2. add level field
    structlog.stdlib.add_logger_name,             # 3. add logger name
    drop_sensitive_fields,                       # 4. your custom processors
    structlog.processors.TimeStamper(fmt="iso"), # 5. timestamp
    structlog.processors.StackInfoRenderer(),    # 6. stack info
    structlog.processors.format_exc_info,        # 7. exception formatting
    # LAST: renderer
    structlog.processors.JSONRenderer(),         # 8. or ConsoleRenderer()
]
```

For unified stdlib integration, use the Section 5 pattern: end structlog's chain
with `ProcessorFormatter.wrap_for_formatter`, run `ExtraAdder` only for foreign
stdlib records, and put the final renderer in `ProcessorFormatter`.

### Logger Usage Cheat Sheet

```python
import structlog
log = structlog.get_logger()

# Basic logging
log.info("event_name", key="value", count=42)

# Bind persistent context
service_log = log.bind(service="payment", version="2.1")

# Async context (visible to all loggers in this request)
structlog.contextvars.bind_contextvars(request_id="abc-123")

# Exception logging
try:
    risky()
except Exception:
    log.exception("operation_failed", operation="risky")

# Remove context
structlog.contextvars.unbind_contextvars("request_id")
```

---

**Next**: [Configuration — typed settings and secrets](configuration.md)
