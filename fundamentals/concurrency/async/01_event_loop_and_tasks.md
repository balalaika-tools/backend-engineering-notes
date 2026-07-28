# Asyncio: Event Loops, Coroutines, and Tasks

> **Who this is for**: Python backend developers who can write synchronous Python but need a precise mental model for `async`, `await`, tasks, and structured concurrency. Start with the [concurrency decision guide](../00_decision_guide.md).

---

## 1. What Asyncio Solves

Network services spend much of their time waiting: for a socket, database response, timer, or client. A thread can wait for each operation, but thousands of threads consume substantial memory and add scheduling overhead. **Asyncio** lets one event-loop thread coordinate many waiting operations.

The event loop repeatedly:

1. Runs a ready task.
2. Keeps running it until the task completes, raises, or awaits an operation that is not ready.
3. Runs another ready task.
4. Resumes the first task when its operation becomes ready.

```text
time ──────────────────────────────────────────────────────────────>

task A   run ── await socket ─────────────── run ── done
task B          run ── await timer ── run ── done
loop     [ A ][ B ][ idle/other work ][ B ][ A ]
```

This is **cooperative concurrency**. A task must reach an await point that actually suspends before another task gets a turn. `await` is not a magic preemption instruction: if the awaited operation is already complete, the current task may continue immediately.

```python
async def bad_handler() -> int:
    # No await occurs in this loop, so every other task waits for it.
    return sum(i * i for i in range(20_000_000))
```

> **Key insight**: Asyncio improves throughput when work waits on async-compatible I/O. It does not make synchronous I/O non-blocking and it does not make Python CPU work parallel.

---

## 2. Coroutine, Task, and Future

These terms describe different layers:

| Term | Meaning | How it runs |
|------|---------|-------------|
| Coroutine function | A function declared with `async def` | Call it to create a coroutine object. |
| Coroutine object | One invocation of a coroutine function | Await it or schedule it exactly once. |
| Task | A coroutine scheduled and owned by an event loop | Created by `TaskGroup.create_task()` or `asyncio.create_task()`. |
| Future | A low-level placeholder for a result that will arrive later | Usually created by asyncio or a library, not application code. |

Calling a coroutine function does not run its body:

```python
import asyncio

async def load_user() -> dict[str, str]:
    await asyncio.sleep(0.1)
    return {"name": "Ada"}

coroutine = load_user()  # Created, but not running.
coroutine.close()        # Close this demonstration object to avoid a warning.
```

In application code, immediately await or schedule the coroutine:

```python
user = await load_user()  # Run it as part of the current task.
```

```python
async with asyncio.TaskGroup() as group:
    task = group.create_task(load_user())  # Run it in a child task.

user = task.result()  # Safe because the TaskGroup has exited.
```

`await load_user()` pauses the current coroutine until `load_user()` finishes. Creating a child task lets the caller and child make progress concurrently, but it also creates a lifecycle that someone must own.

---

## 3. Sequential Await Versus Concurrent Tasks

This complete program makes the timing difference visible:

```python
import asyncio
import time

async def query_service(name: str, delay_s: float) -> str:
    await asyncio.sleep(delay_s)  # Stands in for non-blocking network I/O.
    return f"{name}:ok"

async def sequential() -> list[str]:
    results = []
    for name in ("catalog", "pricing", "inventory"):
        results.append(await query_service(name, 0.2))
    return results

async def concurrent() -> list[str]:
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(query_service(name, 0.2), name=f"query:{name}")
            for name in ("catalog", "pricing", "inventory")
        ]
    return [task.result() for task in tasks]

async def main() -> None:
    for label, operation in (
        ("sequential", sequential),
        ("concurrent", concurrent),
    ):
        started = time.perf_counter()
        results = await operation()
        elapsed = time.perf_counter() - started
        print(f"{label}: {elapsed:.2f}s, {results}")

if __name__ == "__main__":
    asyncio.run(main())
```

The sequential version takes roughly the sum of all waits. The concurrent version takes roughly the longest wait because the waits overlap. Real services also have connection limits, downstream capacity, and request deadlines, so concurrent fan-out must be bounded; the [production patterns guide](02_production_patterns.md) covers that.

---

## 4. Own Related Work with `TaskGroup`

**Structured concurrency** means child tasks cannot silently outlive the scope that created them. `asyncio.TaskGroup` is the default tool for related work in Python 3.11+:

```python
import asyncio

async def build_dashboard(user_id: int) -> dict[str, object]:
    async with asyncio.TaskGroup() as group:
        profile_task = group.create_task(load_profile(user_id))
        orders_task = group.create_task(load_orders(user_id))

    # Exiting the block guarantees both tasks finished successfully.
    return {
        "profile": profile_task.result(),
        "orders": orders_task.result(),
    }
```

If a child raises a non-cancellation exception, the group:

1. Cancels the remaining children.
2. Waits for their cancellation cleanup.
3. Raises the failures as an `ExceptionGroup`.

Handle child failures by type with `except*`:

```python
try:
    async with asyncio.TaskGroup() as group:
        group.create_task(refresh_catalog())
        group.create_task(refresh_prices())
except* TimeoutError as errors:
    for error in errors.exceptions:
        logger.warning("refresh timed out", exc_info=error)
except* ConnectionError as errors:
    for error in errors.exceptions:
        logger.warning("refresh connection failed", exc_info=error)
```

An `ExceptionGroup` can be nested, so production error reporting may need `ExceptionGroup.subgroup()` or recursive formatting rather than assuming every member is a leaf exception.

---

## 5. When `gather()` and `create_task()` Still Fit

`asyncio.gather()` is useful when all of these semantics are intentional:

- Results must be returned in input order.
- A child failure should be propagated immediately.
- Other children should continue instead of being cancelled automatically.

```python
results = await asyncio.gather(
    load_profile(user_id),
    load_orders(user_id),
)
```

With the default `return_exceptions=False`, `gather()` propagates the first observed exception but does **not** cancel the other awaitables. With `return_exceptions=True`, exceptions appear in the result list and must be checked explicitly. That mode is useful for deliberate best-effort batches, but it can turn failures into apparently successful return values.

Use raw `asyncio.create_task()` only when the task lifetime intentionally differs from the current block. Keep a strong reference, define how errors are observed, and arrange shutdown:

```python
import asyncio
from collections.abc import Coroutine
from typing import Any

class TaskSupervisor:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def start(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
```

This supervisor prevents tasks from disappearing, but it still needs application-level logging and a policy for failed tasks. Durable work that must survive process termination belongs in a job queue, not an in-process task.

---

## 6. Cancellation Is a Control Signal

`task.cancel()` requests cancellation. At the next suitable suspension point, asyncio injects `CancelledError` into the task. Cancellation is cooperative: synchronous CPU work and a blocking function cannot be interrupted by the event loop.

Use `try/finally` for cleanup:

```python
import asyncio

async def consume_stream() -> None:
    connection = await open_stream()
    try:
        await connection.consume()
    finally:
        await connection.aclose()
```

If cleanup needs special cancellation handling, catch and re-raise:

```python
async def worker() -> None:
    try:
        await process_messages()
    except asyncio.CancelledError:
        await flush_metrics()
        raise
```

Do not normally suppress `CancelledError`. `TaskGroup` and `asyncio.timeout()` use cancellation internally, so swallowing it can break their guarantees. Cancellation, timeouts, shielding, and shutdown are covered in [Async Production Patterns](02_production_patterns.md).

---

## 7. Async Resource Lifetimes

Use `async with` when acquisition or cleanup must await:

```python
async with make_async_client() as client:
    response = await client.get("/health")
```

Conceptually, the object implements `__aenter__()` and `__aexit__()`:

```python
resource = await manager.__aenter__()
try:
    await use(resource)
finally:
    await manager.__aexit__(None, None, None)
```

The real protocol receives exception information in `__aexit__`; the expansion above only illustrates the lifetime. Common async context managers include HTTP clients, database transactions, streams, locks, timeouts, and `TaskGroup`.

Keep expensive clients and pools at application scope when they are designed for reuse. Creating a connection pool inside every request defeats pooling and increases resource churn.

---

## 8. Cross the Sync Boundary Deliberately

Offload a blocking I/O function with `asyncio.to_thread()`:

```python
import asyncio
import urllib.request

def fetch_sync(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read(1_000_000)

async def fetch_without_blocking_loop(url: str) -> bytes:
    return await asyncio.to_thread(fetch_sync, url)
```

`to_thread()` keeps the event loop responsive and copies the current `contextvars.Context` into the worker. It does not make an unsafe library thread-safe, and cancelling the awaiting asyncio task does not stop a thread that is already executing. The blocking function still needs its own timeout.

For a dedicated pool, isolation between blocking dependencies, or finer sizing, use [ThreadPoolExecutor](../threads/01_thread_pool_executor.md). For pure-Python CPU work, use a long-lived [ProcessPoolExecutor](../processes/01_process_pool_executor.md). Awaiting a process-pool future also does not guarantee that already-running worker code stops on cancellation.

---

## 9. Common Failure Modes and Diagnostics

| Symptom | Likely cause | First check |
|---------|--------------|-------------|
| All requests pause together | Synchronous I/O or CPU work blocks the loop | Enable asyncio debug mode and inspect slow callbacks. |
| `coroutine was never awaited` | A coroutine object was created and discarded | Await it or schedule it in an owned scope. |
| `Task exception was never retrieved` | A detached task failed without supervision | Use `TaskGroup` or retain and inspect the task. |
| Memory and sockets spike during fan-out | Too many tasks or active operations | Add admission control, a bounded queue, and capacity limits. |
| Shutdown hangs | A task ignores cancellation or waits forever | Add deadlines and make waits shutdown-aware. |
| Timeouts exceed the configured duration | Cancellation cleanup is slow or blocking | Inspect the timed operation and its `finally` blocks. |

Useful development commands:

```bash
PYTHONASYNCIODEBUG=1 python app.py
python -X dev app.py
python -m asyncio
```

In code:

```python
loop = asyncio.get_running_loop()
loop.set_debug(True)
loop.slow_callback_duration = 0.05
```

Debug mode is a diagnostic aid, not a production latency monitor. Measure event-loop delay explicitly and correlate it with task counts, queue depth, executor utilization, and downstream latency.

---

## References

- [`asyncio` coroutines and tasks](https://docs.python.org/3/library/asyncio-task.html)
- [`asyncio` event loop](https://docs.python.org/3/library/asyncio-eventloop.html)
- [PEP 654 — Exception Groups and `except*`](https://peps.python.org/pep-0654/)

---

**Next**: [Async Production Patterns](02_production_patterns.md)
