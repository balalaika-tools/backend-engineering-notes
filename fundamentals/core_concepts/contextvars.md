# ContextVars — Request-Scoped State in Async Python

In async Python, you often need **per-request state** (request ID, current user, DB session) that flows through the entire call stack — without passing it as an argument to every function.

Think of it as:

> "An invisible argument that every function in the call chain can read, scoped to the current request or task."

The `contextvars` module (stdlib since Python 3.7) solves this cleanly for sync, threaded, **and** async code.

---

## The Problem

### Global variables are shared across all requests

```python
current_user = None  # module-level global

async def handle_request(user: str):
    global current_user
    current_user = user
    await asyncio.sleep(0.1)          # other tasks run here
    print(f"Handling: {current_user}") # ❌ may print wrong user
```

Two concurrent requests overwrite each other's `current_user`. Globals are **shared across all coroutines** — in an async server handling hundreds of concurrent requests, this is a data race waiting to happen.

### `threading.local()` works for threads but breaks with async

```python
import threading
import asyncio

_local = threading.local()

async def handle_request(uid: str):
    _local.user_id = uid
    await asyncio.sleep(0.1)
    # ❌ Another coroutine on the SAME thread may have overwritten this
    print(f"Task {uid} sees: {_local.user_id}")

async def main():
    # Both tasks share the same thread → same threading.local() storage
    async with asyncio.TaskGroup() as tg:
        tg.create_task(handle_request("alice"))
        tg.create_task(handle_request("bob"))

asyncio.run(main())
```

Output (non-deterministic):

```
Task alice sees: bob   ← wrong!
Task bob sees: bob
```

Why? `asyncio` runs many coroutines on **one thread**. `threading.local()` is per-thread, so all coroutines share the same slot.

> **The core issue:** We need state scoped to a **logical execution context** (a request, a task), not to a physical thread or process.

---

## The `contextvars` Module (Python 3.7+)

The stdlib `contextvars` module provides these building blocks:

| API | Purpose |
|-----|---------|
| `ContextVar(name, default=...)` | Declare a context variable |
| `var.set(value)` | Set value in the current context, returns a `Token` |
| `var.get(default=...)` | Get current value (or default) |
| `var.reset(token)` | Undo a `.set()` using the token it returned |
| `token.old_value` | The value before the `.set()` that created this token |
| `contextvars.copy_context()` | Snapshot the entire current context into a `Context` object |
| `context.run(fn, *args)` | Run a function inside a specific context snapshot |

How it works under the hood:

* Every **asyncio Task** gets its own `Context` copy when it is created.
* When a Task runs, its `Context` is the "active" context.
* `.set()` only modifies the **current Task's context** — other Tasks are unaffected.
* `copy_context()` takes a snapshot you can pass to threads or sub-tasks.

---

## Basic Usage (Sync)

### Creating and using a ContextVar

```python
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar('request_id', default='no-request')

# set and get
token = request_id.set('abc-123')
print(request_id.get())  # abc-123

# reset
request_id.reset(token)
print(request_id.get())  # no-request
```

### Using defaults

```python
request_id: ContextVar[str] = ContextVar('request_id', default='unknown')

print(request_id.get())          # unknown (no .set() called yet)

token = request_id.set('req-42')
print(request_id.get())          # req-42

request_id.reset(token)
print(request_id.get())          # unknown (back to default)
```

### Token-based reset in a context manager

A common pattern — wrap set/reset so it always cleans up:

```python
from contextvars import ContextVar, Token
from contextlib import contextmanager

request_id: ContextVar[str] = ContextVar('request_id', default='N/A')

@contextmanager
def request_scope(rid: str):
    token: Token = request_id.set(rid)
    try:
        yield
    finally:
        request_id.reset(token)
```

Usage:

```python
with request_scope("req-123"):
    print(request_id.get())   # req-123
    do_something()             # can also read req-123

print(request_id.get())       # N/A (reverted)
```

---

## ContextVars in Async Code

Key insight: **`asyncio.create_task()` copies the current context into the new task.** Each task gets an isolated snapshot.

```python
import asyncio
from contextvars import ContextVar

user_id: ContextVar[str] = ContextVar('user_id')

async def handle_request(uid: str):
    user_id.set(uid)
    await asyncio.sleep(0.1)  # simulate work — other tasks run here
    # Still sees its own value, even though other tasks ran concurrently
    print(f"Task {uid} sees: {user_id.get()}")

async def main():
    # These run concurrently but each sees its own user_id
    async with asyncio.TaskGroup() as tg:
        tg.create_task(handle_request("alice"))
        tg.create_task(handle_request("bob"))

asyncio.run(main())
```

Output (always correct):

```
Task alice sees: alice ✅
Task bob sees: bob     ✅
```

### Why does this work?

1. `main()` calls `tg.create_task(handle_request("alice"))`
2. `create_task()` **snapshots** the current context and attaches it to the new `Task`
3. When the task runs, it operates on its **own copy** of the context
4. `user_id.set(uid)` modifies only **that task's** copy
5. After `await`, the event loop restores the correct context before resuming the coroutine

This is built into the event loop — no opt-in required.

### Contrast: tasks don't leak into each other or into `main`

```python
import asyncio
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar('request_id', default='N/A')

async def worker(name: str):
    print(f"{name} sees: {request_id.get()}")
    request_id.set(f"modified-by-{name}")
    print(f"{name} changed to: {request_id.get()}")

async def main():
    request_id.set("original")

    task1 = asyncio.create_task(worker("task1"))
    task2 = asyncio.create_task(worker("task2"))
    await asyncio.gather(task1, task2)

    print(f"main still sees: {request_id.get()}")

asyncio.run(main())
```

Output:

```
task1 sees: original
task1 changed to: modified-by-task1
task2 sees: original
task2 changed to: modified-by-task2
main still sees: original
```

What happened:

1. `main` sets the value to `"original"`.
2. Each task gets a **copy** of the context at creation time — both see `"original"`.
3. Each task modifies its own copy — the changes are isolated.
4. `main`'s context is unchanged.

---

## ContextVars in Threaded Code

Context does **NOT** auto-propagate to threads you spawn manually. If you create a raw thread, it starts with an empty/default context.

### The problem

```python
import threading
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar('request_id', default='none')

request_id.set('abc-123')

def worker():
    # ❌ This sees 'none' (the default), not 'abc-123'
    print(f"Worker sees: {request_id.get()}")

t = threading.Thread(target=worker)
t.start()
t.join()
```

### The solution: `copy_context().run()`

```python
import contextvars
import threading
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar('request_id', default='none')

request_id.set('abc-123')

ctx = contextvars.copy_context()

def worker():
    print(f"Worker sees: {request_id.get()}")  # ✅ abc-123

t = threading.Thread(target=ctx.run, args=(worker,))
t.start()
t.join()
```

### The `run_in_executor` pattern (common in FastAPI)

When you call a sync function from async code using `run_in_executor`, **asyncio copies context automatically**:

```python
import asyncio
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar('request_id', default='none')

def sync_db_query():
    # ✅ This works — asyncio copies context into the executor thread
    rid = request_id.get()
    print(f"DB query for request: {rid}")

async def handle_request():
    request_id.set('abc-123')
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, sync_db_query)
```

`asyncio.loop.run_in_executor()` automatically wraps the callable with `copy_context().run()` **only when you use the default executor** (pass `None` as the first argument). If you pass a **custom** `Executor` — a ThreadPoolExecutor you created yourself, a ProcessPoolExecutor, etc. — the event loop does not copy context for you, and the executor thread will see empty ContextVars. In that case, or if you use a raw `ThreadPoolExecutor` outside of asyncio, you need to copy context yourself:

```python
from contextvars import ContextVar, copy_context
from concurrent.futures import ThreadPoolExecutor

request_id: ContextVar[str] = ContextVar('request_id', default='N/A')

def blocking_work():
    print(f"Thread sees: {request_id.get()}")

def main():
    request_id.set("req-xyz")
    ctx = copy_context()

    with ThreadPoolExecutor() as pool:
        # ✅ Run inside the copied context
        future = pool.submit(ctx.run, blocking_work)
        future.result()  # Thread sees: req-xyz

main()
```

The same `ctx.run(...)` wrapping is required when you hand a custom executor to `loop.run_in_executor(custom_executor, fn)` — the auto-copy only applies to the default executor.

---

## Practical Patterns

### Pattern 1: Request ID Middleware (FastAPI)

Set a request ID in middleware, read it anywhere in the request handler chain — no need to pass it through every function.

```python
# context.py
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar('request_id', default='')
```

```python
# middleware.py
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from context import request_id_var

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get('X-Request-ID', str(uuid.uuid4()))
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
            response.headers['X-Request-ID'] = rid
            return response
        finally:
            request_id_var.reset(token)
```

```python
# main.py
from fastapi import FastAPI
from middleware import RequestIDMiddleware
from context import request_id_var

app = FastAPI()
app.add_middleware(RequestIDMiddleware)

@app.get("/items")
async def get_items():
    return {"request_id": request_id_var.get(), "items": []}
```

```python
# services/item_service.py — deep in the call stack
from context import request_id_var

async def fetch_items():
    req_id = request_id_var.get()  # ✅ Works! No argument passing needed.
    print(f"[{req_id}] Fetching items from DB...")
    return []
```

### Pattern 2: Structured Logging with `structlog`

`structlog` has first-class `contextvars` support. Bind request-scoped data once, and every subsequent log call includes it automatically:

```python
import structlog

# In your middleware / request handler — clean slate for this request:
structlog.contextvars.clear_contextvars()
structlog.contextvars.bind_contextvars(
    request_id=rid,
    user_id=uid,
)

# Anywhere deeper in the call stack:
logger = structlog.get_logger()
logger.info("order_created", order_id=order.id)
# Output includes request_id and user_id automatically:
# {"event": "order_created", "order_id": 42, "request_id": "abc-123", "user_id": 7}
```

Under the hood, `structlog.contextvars` uses a `ContextVar[dict]` to store the bound key-value pairs. Since each asyncio task has its own context, bound values are isolated per-request.

You can also write a custom structlog processor that reads from your own `ContextVar`:

```python
from contextvars import ContextVar
import structlog

request_id_var: ContextVar[str] = ContextVar("request_id", default="N/A")

def add_request_id(logger, method_name, event_dict):
    """structlog processor that injects request_id from ContextVar."""
    event_dict["request_id"] = request_id_var.get()
    return event_dict

structlog.configure(
    processors=[
        add_request_id,
        structlog.dev.ConsoleRenderer(),
    ]
)
```

### Pattern 3: Database Session Context

Useful in complex codebases where passing the session through every function is impractical:

```python
from contextvars import ContextVar
from sqlalchemy.ext.asyncio import AsyncSession

db_session_var: ContextVar[AsyncSession] = ContextVar('db_session')

# Middleware sets it per-request
async def db_session_middleware(request, call_next):
    async with async_session_maker() as session:
        token = db_session_var.set(session)
        try:
            response = await call_next(request)
            await session.commit()
            return response
        except Exception:
            await session.rollback()
            raise
        finally:
            db_session_var.reset(token)

# Deep in your code — no session argument needed
async def get_user_by_id(user_id: int):
    session = db_session_var.get()
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
```

> **Caveat:** Explicit dependency injection (e.g., FastAPI's `Depends()`) is usually preferred over implicit contextvar-based sessions. Use this pattern when DI becomes impractical (deeply nested calls, third-party code, etc.).

---

## ContextVars and Background Tasks

### FastAPI `BackgroundTasks` — context IS propagated

Background tasks run in the same event loop (or threadpool). Starlette copies the context:

```python
from fastapi import BackgroundTasks
from contextvars import ContextVar, copy_context

request_id_var: ContextVar[str] = ContextVar('request_id', default='N/A')

def send_confirmation_email(to: str):
    rid = request_id_var.get()
    print(f"[{rid}] Sending email to {to}")

@app.post("/register")
async def register(bg_tasks: BackgroundTasks):
    request_id_var.set("req-register-42")

    # To be safe, capture context explicitly:
    ctx = copy_context()
    bg_tasks.add_task(ctx.run, send_confirmation_email, "user@example.com")
    return {"status": "ok"}
```

### Celery / Dramatiq workers — context is NOT propagated

Task queue workers run in **separate processes**. ContextVars live in process memory — they don't serialize across a message queue.

**The pattern: serialize context -> send as message header -> restore in worker**

```python
# === Producer side (FastAPI) ===
from context import request_id_var

def enqueue_task(order_id: int):
    rid = request_id_var.get()
    # Pass context as message headers
    process_order.apply_async(
        args=[order_id],
        headers={"request_id": rid},
    )
```

```python
# === Consumer side (Celery worker) ===
from celery.signals import task_prerun
from context import request_id_var

@task_prerun.connect
def restore_context(sender, headers=None, **kwargs):
    """Celery signal: runs before every task."""
    if headers and "request_id" in headers:
        request_id_var.set(headers["request_id"])

@app.task
def process_order(order_id: int):
    rid = request_id_var.get()  # ✅ restored from message header
    print(f"[{rid}] Processing order {order_id}")
```

Alternative (simpler, works with Dramatiq too): just pass the values as regular task arguments:

```python
@dramatiq.actor
def process_order(order_id: int, request_id: str):
    token = request_id_var.set(request_id)
    try:
        print(f"[{request_id}] Processing order {order_id}")
    finally:
        request_id_var.reset(token)

# Dispatching:
req_id = request_id_var.get()
process_order.send(order_id=42, request_id=req_id)
```

> **Rule of thumb:** If the work crosses a process boundary (Celery, Dramatiq, subprocess), you must **serialize and pass** the context values explicitly. ContextVars don't magically cross process boundaries.

---

## Comparison Table

| Mechanism | Sync threads | Async tasks | New threads (manual) | Subprocess / worker |
|-----------|-------------|-------------|----------------------|---------------------|
| Global variable | Shared (broken) | Shared (broken) | Shared (broken) | Isolated |
| `threading.local()` | Isolated | **Shared (broken)** | Isolated | N/A |
| `ContextVar` | Isolated* | **Isolated** | Not propagated** | Not propagated |

\* `ContextVar` in sync threads: isolated **if** using `copy_context().run()`, otherwise shares the main thread's context.

\** You must manually call `copy_context()` and use `ctx.run()` in the new thread. `asyncio.loop.run_in_executor()` does this automatically.

---

## Common Mistakes

### Mistake 1: Setting at module level

```python
# ❌ This runs once at import time — affects ALL requests
request_id: ContextVar[str] = ContextVar('request_id')
request_id.set("oops")  # set in module scope = shared default for main context
```

**Declare** the `ContextVar` at module level (that's fine and expected). But **`.set()`** should happen inside a request handler, middleware, or task — not at import time.

### Mistake 2: Forgetting to reset (token pattern)

```python
# ❌ If do_work() raises, the value leaks into subsequent code in the same context
request_id.set("abc-123")
do_work()
```

```python
# ✅ Always reset in a finally block
token = request_id.set("abc-123")
try:
    do_work()
finally:
    request_id.reset(token)
```

In practice, asyncio tasks are short-lived (one per request), so leaking within a task is rarely a problem. The token pattern matters most in **middleware** and **reusable utilities** that shouldn't permanently modify the caller's context.

### Mistake 3: Assuming propagation to sub-processes

```python
# ❌ Celery/Dramatiq workers are separate processes — context doesn't cross
request_id_var.set("abc-123")
my_celery_task.delay()  # worker has no idea about request_id_var
```

### Mistake 4: Not copying context when spawning threads manually

```python
# ❌ Thread gets empty/default context
threading.Thread(target=worker).start()

# ✅ Copy context explicitly
ctx = contextvars.copy_context()
threading.Thread(target=ctx.run, args=(worker,)).start()
```

---

## 🔟 Mental Model

> **ContextVars = "async-safe thread-locals".**
>
> * Each `asyncio` Task automatically gets its own copy.
> * For threads, you copy explicitly with `copy_context().run()`.
> * For processes, you serialize and restore manually.

```text
Request comes in
  └─ Middleware sets ContextVar
       └─ Handler reads it ✅
            ├─ await sub_call() → same task, same context ✅
            ├─ create_task()   → NEW task, gets a COPY ✅
            ├─ run_in_executor() → new thread, asyncio copies for you ✅
            ├─ Thread(target=fn) → ❌ must copy_context().run() yourself
            └─ celery_task.delay() → ❌ separate process, must serialize
```

### When to use ContextVars

* **Request ID / correlation ID** — the #1 use case
* **Current user / auth context** — avoid passing `user` through 10 layers
* **Database session** — scoped to a request
* **Structured logging** — auto-inject request metadata into every log line
* **Feature flags** — evaluated once per request, available everywhere

### When NOT to use ContextVars

* **Passing data between two specific functions** — just use function arguments
* **Sharing state across processes** — use Redis, a database, or message queues
* **Global configuration** — use regular module-level constants or settings objects

---

## ✅ TL;DR

1. **Declare** `ContextVar` at module level (like you would a logger).
2. **Set** inside middleware or request handlers with `token = var.set(value)`.
3. **Read** anywhere in the call chain with `var.get()`.
4. **Reset** in a `finally` block with `var.reset(token)`.
5. **Background tasks** in the same process: use `copy_context().run()`.
6. **Cross-process workers** (Celery, Dramatiq): pass values as explicit arguments.

```python
# The whole pattern in 10 lines
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="N/A")

# In middleware
token = request_id_var.set("req-123")
try:
    response = await handle_request()
finally:
    request_id_var.reset(token)

# Anywhere else in the request chain
def get_request_id() -> str:
    return request_id_var.get()
```

---
