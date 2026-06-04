# Production Concurrency Patterns

The earlier files cover *how* threads, processes, and asyncio work. This file covers the patterns you need the moment code leaves your laptop — **bounding**, **timing out**, **cancelling**, **propagating context**, and **shutting down cleanly**.

---

## The core problem: unbounded concurrency

A naive `TaskGroup` fan-out is unbounded — one request can spawn 10,000 coroutines and exhaust sockets, file descriptors, or downstream rate limits.

```python
# ❌ unbounded — this will melt a downstream service
async with asyncio.TaskGroup() as tg:
    for url in urls:
        tg.create_task(fetch(url))
```

The fix is almost always an **`asyncio.Semaphore`**.

---

## Bounding concurrency with `asyncio.Semaphore`

```python
import asyncio

sem = asyncio.Semaphore(50)   # at most 50 concurrent fetches

async def fetch_bounded(url: str) -> bytes:
    async with sem:
        return await fetch(url)

async def main(urls):
    async with asyncio.TaskGroup() as tg:
        for url in urls:
            tg.create_task(fetch_bounded(url))
```

Rules of thumb:

* **External HTTP** → semaphore = (concurrent connection budget from the provider)
* **Database** → semaphore ≤ connection pool size
* **Disk I/O** → 2–8 is usually plenty
* **Always** bound anything that fans out over user input

---

## Producer / consumer with `asyncio.Queue`

`Queue` is the right tool when producers and consumers run at different rates, or when you need backpressure inside a single process.

```python
import asyncio
from typing import Optional

async def producer(q: "asyncio.Queue[Optional[int]]"):
    for i in range(100):
        await q.put(i)          # blocks when queue is full → natural backpressure
    await q.put(None)           # sentinel

async def consumer(q: "asyncio.Queue[Optional[int]]"):
    while True:
        item = await q.get()
        if item is None:
            q.task_done()
            break
        await handle(item)
        q.task_done()

async def main():
    q: "asyncio.Queue[Optional[int]]" = asyncio.Queue(maxsize=10)
    async with asyncio.TaskGroup() as tg:
        tg.create_task(producer(q))
        tg.create_task(consumer(q))
```

Key points:

* `maxsize` is the backpressure knob. Without it, a fast producer silently buffers unbounded memory.
* With multiple consumers, send one sentinel per consumer or use explicit cancellation after `q.join()`.

---

## Timeouts — prefer `asyncio.timeout()` for scoped budgets

`asyncio.timeout()` (3.11+) is the modern context-manager form. It composes with `TaskGroup`, makes the timed region obvious, and produces cleaner tracebacks.

```python
import asyncio

async def call_with_budget():
    try:
        async with asyncio.timeout(2.0):      # 2-second budget
            return await slow_api()
    except TimeoutError:
        return fallback()
```

Dynamic deadlines (e.g. propagating an upstream deadline):

```python
async with asyncio.timeout_at(deadline_monotonic):
    ...
```

`asyncio.wait_for(coro, timeout=...)` is still supported and useful for timing out one awaitable. Prefer `asyncio.timeout()` when you want a deadline around a larger block of work.

---

## Cancellation: `CancelledError` is not a regular exception

In asyncio, cancellation is delivered as `asyncio.CancelledError`. In Python 3.8+ it inherits from `BaseException`, **not** `Exception` — so a bare `except Exception:` will not swallow it (good).

The dangerous pattern is:

```python
# ❌ breaks cancellation
try:
    await something()
except BaseException:
    log.exception("oops")
    # CancelledError is silently swallowed — task never stops
```

Rules:

1. **Do not catch `BaseException`** unless you also re-raise `CancelledError`.
2. Use `try / finally` for cleanup that must run on cancellation — not `try / except`.
3. If you must catch `CancelledError` (e.g. for structured cleanup), **re-raise it**:

```python
async def guarded():
    try:
        await work()
    except asyncio.CancelledError:
        await cleanup()
        raise
```

---

## `asyncio.shield` — when a task must survive its caller

Occasionally the caller is cancelled but the work must finish anyway (e.g. a DB write that's already in flight). `shield` protects the inner task from the outer cancellation, but you should keep a strong reference to the task:

```python
async def commit():
    task = asyncio.create_task(write_to_db())
    await asyncio.shield(task)
```

Use it sparingly. If every call is shielded, cancellation stops meaning anything.

---

## Request context with `contextvars`

`contextvars` is the right default for carrying request-scoped state (trace IDs, tenant, user) through async code. `threading.local` is per-thread, so it does not model per-task async context.

```python
import contextvars

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id")

async def handler(req):
    token = request_id.set(req.headers["x-request-id"])
    try:
        await do_work()       # can read request_id.get() anywhere in the call tree
    finally:
        request_id.reset(token)

async def do_work():
    log.info("processing", extra={"request_id": request_id.get()})
```

`asyncio.create_task()` **copies** the current context into the new task — so tasks see the trace ID that was active when they were created. `asyncio.to_thread()` also propagates the current context into the worker thread.

---

## Graceful shutdown (SIGTERM)

Production servers must drain in-flight work when the orchestrator (Kubernetes, systemd) sends `SIGTERM`. The pattern:

```python
import asyncio, signal

async def main():
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(server(stop))
        await stop.wait()
        # On normal exit, TaskGroup waits for server() to finish.

async def server(stop: asyncio.Event):
    while not stop.is_set():
        await handle_one_request()
```

Uvicorn / Gunicorn already handle this for HTTP workers — you generally only wire it up yourself in long-running scripts and workers.

If the worker can block forever waiting for new input, make that wait cancellable or wake it when `stop` is set. `TaskGroup` cancels sibling tasks when a task fails; it does not cancel healthy tasks just because the `async with` body reached the end.

---

## Never block the event loop

Any synchronous call that takes more than ~10 ms blocks **every** coroutine on that loop. Common offenders:

* `requests.get(...)` — use `httpx.AsyncClient`
* `time.sleep(...)` — use `await asyncio.sleep(...)`
* CPU-heavy pure-Python loops — offload to `ProcessPoolExecutor`
* Synchronous DB drivers — use the async driver (`asyncpg`, `aiosqlite`, etc.)
* Large JSON parsing of multi-MB payloads — `await asyncio.to_thread(json.loads, blob)`

### Detecting blockers

Set this env var in dev — the loop will log any callback that runs longer than 100 ms:

```bash
PYTHONASYNCIODEBUG=1
```

Or in code:

```python
loop = asyncio.get_running_loop()
loop.set_debug(True)
loop.slow_callback_duration = 0.05   # 50 ms
```

In production, track the event-loop lag metric (`asyncio`'s `slow_callback_duration` warnings, or a dedicated gauge) — rising lag is the leading indicator that something synchronous snuck into an async handler.

---

## Thread-safety primitives (for threaded code)

When you do use threads, the basics:

| Primitive             | Use for                                           |
|-----------------------|---------------------------------------------------|
| `threading.Lock`      | Mutual exclusion around shared mutable state      |
| `threading.RLock`     | Same, but the same thread can re-acquire it       |
| `threading.Event`     | One-shot "go" signal between threads              |
| `queue.Queue`         | Thread-safe producer / consumer queue             |
| `threading.local`     | Per-thread storage (does **not** cross `await`)   |

In the free-threaded (`python3.14t`) build, you need these for real. On the GIL build, many racy patterns appeared to work because the GIL serialized bytecode execution, but that was never a good synchronization contract.

---

## Subinterpreters (PEP 734, Python 3.13+)

A third option alongside threads and processes: each subinterpreter is an isolated Python interpreter **in the same process**, with its own GIL. `concurrent.futures.InterpreterPoolExecutor` landed in Python 3.14. It can provide true multi-core parallelism on the default GIL build, but callables, arguments, and return values are still serialized with `pickle`, and mutable Python objects cannot be shared directly across interpreters.

Use when:

* You want CPU parallelism on the default (GIL) build
* Process startup overhead is a real problem
* You can accept a young API

Skip when:

* Free-threaded Python already works for your dependency graph
* Multiprocessing is already fast enough

---

## Production checklist

* [ ] Every fan-out over user input is bounded (semaphore or queue)
* [ ] Every external call has a timeout
* [ ] No bare `except:` or `except BaseException:` around awaits
* [ ] Request context flows via `contextvars`, not `threading.local`
* [ ] SIGTERM drains in-flight work instead of dropping it
* [ ] `slow_callback_duration` alarms wired up in staging
* [ ] Process pools are module-level (not per-request), and shut down on exit
* [ ] Async code uses async drivers — no `requests`, no `time.sleep`, no sync DB libs on the hot path

---

## References

* [PEP 654 — Exception Groups and `except*`](https://peps.python.org/pep-0654/)
* [PEP 703 — Making the GIL Optional](https://peps.python.org/pep-0703/)
* [PEP 734 — Multiple Interpreters in the Stdlib](https://peps.python.org/pep-0734/)
* [PEP 779 — Supported status for free-threaded Python](https://peps.python.org/pep-0779/)
* [`asyncio` tasks, cancellation, timeouts, and `TaskGroup`](https://docs.python.org/3/library/asyncio-task.html)
* [`asyncio` synchronization primitives](https://docs.python.org/3/library/asyncio-sync.html)
* [`contextvars`](https://docs.python.org/3/library/contextvars.html)
