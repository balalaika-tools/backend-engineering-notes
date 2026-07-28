# Context Variables and Request-Scoped State

> **Who this is for**: Python backend developers who need request IDs, tenant IDs, or trace metadata to follow an async call chain without becoming shared globals. Assumes you understand [tasks and task creation](01_event_loop_and_tasks.md).

---

## 1. The Scope Problem

A module global has process-wide reach. Concurrent requests can overwrite it:

```python
import asyncio

current_request_id = "unset"

async def handle(request_id: str) -> None:
    global current_request_id
    current_request_id = request_id
    await asyncio.sleep(0)
    print(request_id, "observed", current_request_id)

async def main() -> None:
    async with asyncio.TaskGroup() as group:
        group.create_task(handle("req-a"))
        group.create_task(handle("req-b"))

if __name__ == "__main__":
    asyncio.run(main())
```

Both tasks use one global binding, so at least one can observe the other request's value.

`threading.local()` does not solve this for async code. It separates values by OS thread, while many asyncio tasks normally share the event-loop thread.

A **context variable** stores a binding in the current logical execution context. Asyncio restores the correct context whenever it resumes a task.

> **Key insight**: `ContextVar` is for ambient metadata scoped to an execution context. It is not shared storage, dependency injection, or a resource-lifetime manager.

---

## 2. Declare, Set, Read, and Reset

Declare each `ContextVar` once at module scope:

```python
# request_context.py
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar(
    "request_id",
    default="no-request",
)
```

Set it at the boundary and reset it with the returned token:

```python
from request_context import request_id_var

async def handle_request(request_id: str) -> None:
    token = request_id_var.set(request_id)
    try:
        await dispatch_request()
    finally:
        request_id_var.reset(token)
```

Reset restores the value that existed before this particular `set()`. It is safer than assigning a guessed default because scopes can nest:

```python
outer_token = request_id_var.set("outer")
try:
    inner_token = request_id_var.set("inner")
    try:
        assert request_id_var.get() == "inner"
    finally:
        request_id_var.reset(inner_token)
    assert request_id_var.get() == "outer"
finally:
    request_id_var.reset(outer_token)
```

In Python 3.14+, the token is also a context manager:

```python
with request_id_var.set("req-123"):
    await dispatch_request()
```

Use the explicit token pattern when supporting Python 3.11–3.13.

For required context, omit the default so an unbound `.get()` raises `LookupError`. For diagnostics such as logging, a visible sentinel default is often more useful than failing the request.

---

## 3. Task Creation Copies Bindings

Asyncio tasks capture a shallow copy of the current context when the task is created:

```python
import asyncio
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="unset")

async def report(name: str) -> None:
    await asyncio.sleep(0)
    print(name, request_id.get())

async def main() -> None:
    request_id.set("before")
    first = asyncio.create_task(report("first"))

    request_id.set("after")
    second = asyncio.create_task(report("second"))

    await first
    await second

if __name__ == "__main__":
    asyncio.run(main())
```

Output:

```text
first before
second after
```

The important moment is task **creation**, not the first time the task gets CPU time. Later changes in the parent do not rewrite the child's binding, and changes in the child do not rewrite the parent's binding.

Normal `await` does not create a task:

```python
async def parent() -> None:
    request_id.set("req-123")
    await child()  # Same task and same context.
```

`TaskGroup.create_task()` follows the same copy-at-creation rule. Its optional `context=` argument can supply an explicit `contextvars.Context` when isolation must be controlled manually.

---

## 4. A Context Copy Is Shallow

Context isolation applies to the **binding**, not to the internals of the bound value:

```python
from contextvars import ContextVar

tags_var: ContextVar[list[str]] = ContextVar("tags")

shared_tags: list[str] = []
tags_var.set(shared_tags)

# A child task receives another binding to the same list object.
```

If parent and child contexts both point to `shared_tags`, either can mutate the same list. Prefer small immutable values:

```python
request_id_var: ContextVar[str]
tenant_id_var: ContextVar[str]
trace_flags_var: ContextVar[frozenset[str]]
```

This is especially important for async database sessions. A child task inherits the binding to the same session object, not a cloned session. Most transaction/session objects have one mutable state machine and must not be used concurrently.

```python
# ❌ Context propagation makes this easy to reach, not safe to share.
session = session_var.get()
async with asyncio.TaskGroup() as group:
    group.create_task(load_profile(session))
    group.create_task(load_orders(session))
```

Use explicit parameters and separate sessions/transactions when operations truly run concurrently. A `ContextVar` can hide a dependency; it cannot change that dependency's ownership rules.

---

## 5. Propagation Across Boundaries

| Boundary | What happens | What to do |
|----------|--------------|------------|
| Normal function call or `await` in one task | Same current context | Nothing special |
| `asyncio.create_task()` / `TaskGroup.create_task()` | Shallow context copy at creation | Set metadata before creating the task |
| `asyncio.to_thread()` | Current context is propagated to the worker call | Preferred async-to-thread bridge |
| `loop.run_in_executor()` | Do not rely on automatic propagation | Wrap a fresh `copy_context().run` |
| Raw `threading.Thread` | Defaults vary by Python build and flags in 3.14+ | Pass/copy context explicitly |
| Process pool, subprocess, broker worker | No useful automatic propagation | Serialize the required values |

`asyncio.to_thread()` is the simplest thread bridge:

```python
import asyncio

def blocking_sdk_call() -> None:
    logger.info("calling SDK", extra={"request_id": request_id_var.get()})

async def call_sdk() -> None:
    await asyncio.to_thread(blocking_sdk_call)
```

For `run_in_executor()`, copy context for each submission:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="legacy-sdk")

async def call_sdk_with_pool() -> None:
    loop = asyncio.get_running_loop()
    context = copy_context()
    await loop.run_in_executor(pool, context.run, blocking_sdk_call)
```

Do not run the same `Context` concurrently in multiple threads. A context cannot be entered twice at the same time. Create a new `copy_context()` for every independently submitted call.

For raw threads on Python 3.11–3.13:

```python
import threading
from contextvars import copy_context

context = copy_context()
thread = threading.Thread(
    target=context.run,
    args=(blocking_sdk_call,),
    name="legacy-sdk",
)
thread.start()
thread.join()
```

Python 3.14 adds `Thread(..., context=copy_context())`. Its default inheritance behavior differs between regular and free-threaded builds, so passing the context explicitly is the portable choice when correctness depends on propagation.

---

## 6. Cross-Process Context Is Message Data

A process has a separate interpreter and context stack. Job workers may also run much later, after the originating request is gone. Pass only the values the job needs:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class RebuildIndexJob:
    tenant_id: str
    correlation_id: str
    index_name: str

def enqueue_rebuild(index_name: str) -> None:
    job = RebuildIndexJob(
        tenant_id=tenant_id_var.get(),
        correlation_id=request_id_var.get(),
        index_name=index_name,
    )
    job_broker.send(job)
```

At the worker boundary, bind metadata only for the duration of the job:

```python
def handle_rebuild(job: RebuildIndexJob) -> None:
    tenant_token = tenant_id_var.set(job.tenant_id)
    request_token = request_id_var.set(job.correlation_id)
    try:
        rebuild_index(job.index_name)
    finally:
        request_id_var.reset(request_token)
        tenant_id_var.reset(tenant_token)
```

Do not serialize secrets, authorization objects, or a whole request context for convenience. Define an allowlist of low-sensitivity tracing and tenancy fields, validate them at the consumer, and use the worker's own authorization rules.

---

## 7. FastAPI Request-ID Boundary

Set request metadata at the outer request boundary so every downstream call sees it:

```python
import re
import uuid

from fastapi import FastAPI, Request, Response

from request_context import request_id_var

app = FastAPI()
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

def choose_request_id(candidate: str | None) -> str:
    if candidate is not None and REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex

@app.middleware("http")
async def bind_request_id(request: Request, call_next) -> Response:
    request_id = choose_request_id(request.headers.get("x-request-id"))
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
    finally:
        request_id_var.reset(token)
```

Validation prevents an attacker-controlled header from injecting arbitrary characters or unbounded data into logs. In a zero-trust deployment, generate your own public request ID and store an upstream value separately.

An in-process child task copies the request context, but that does not extend the lifetime of request-scoped resources. After the response, middleware may close database sessions, streams, and dependency scopes. Pass durable identifiers to longer-lived work and reacquire resources inside that work.

---

## 8. Logging Without Hidden Business Dependencies

Request and trace IDs are good ambient context because they annotate almost every log line and do not decide business behavior:

```python
import logging

class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True
```

Configure the formatter to include `%(request_id)s`, or bind the value through a structured-logging processor. See the [Structured Logging guide](../../core_concepts/structlog_guide.md) for a complete logging pipeline.

Prefer explicit arguments for values that change results:

```python
# ✅ The authorization dependency is visible and testable.
async def cancel_order(order_id: int, principal: Principal) -> None:
    ...
```

Using `current_user_var.get()` deep inside domain code hides authorization dependencies and makes tests order-sensitive. A practical split is:

- Ambient diagnostics: request ID, trace ID, logging tags.
- Explicit business inputs: user/principal, database session, money, feature decisions.

---

## 9. Failure Modes and Tests

| Failure | Why it happens | Fix |
|---------|----------------|-----|
| Request metadata leaks into later work | Boundary set a value without resetting it | Reset the returned token in `finally` |
| Child sees an older value | Task was created before the parent changed the binding | Set values before task creation |
| Two tasks mutate the same value | The copied binding points to one mutable object | Store immutable metadata |
| Executor logs have no request ID | `run_in_executor()` did not propagate context | Submit `copy_context().run` |
| Process worker has no trace fields | Process boundaries do not copy context | Put allowlisted fields in the message |
| Background work uses a closed session | Context copied the object, not its lifetime | Pass IDs and acquire a fresh resource |

Keep test bindings scoped:

```python
def test_render_log_record() -> None:
    token = request_id_var.set("test-request")
    try:
        assert render_log_record()["request_id"] == "test-request"
    finally:
        request_id_var.reset(token)
```

Also test two concurrent tasks with different values. A serial unit test will not reveal accidental use of a module global or `threading.local()`.

---

## References

- [`contextvars`](https://docs.python.org/3/library/contextvars.html)
- [`asyncio.to_thread()`](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread)
- [`loop.run_in_executor()`](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor)
- [`threading.Thread`](https://docs.python.org/3/library/threading.html#threading.Thread)

---

**Next**: [Threads and Blocking I/O](../threads/README.md)
