# Async Production Patterns

> **Who this is for**: Developers who understand coroutines and tasks and now need async code that stays bounded, cancellable, observable, and correct under load. Read [Asyncio: Event Loops, Coroutines, and Tasks](01_event_loop_and_tasks.md) first.

---

## 1. Bound Both Work and Waiting

Concurrency consumes finite resources: sockets, connection-pool slots, memory, file descriptors, downstream quota, and database capacity. A service fails under load when it admits work faster than it can finish it.

There are two different limits:

| Limit | Protects | Typical tool |
|-------|----------|--------------|
| Active-operation limit | Downstream or local resource capacity | `asyncio.Semaphore` or the client's own pool |
| Waiting-work limit | Application memory and latency | `asyncio.Queue(maxsize=...)` or rejection at ingress |

A semaphore limits how many operations enter a critical region:

```python
import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

async def map_bounded(
    items: Iterable[T],
    operation: Callable[[T], Awaitable[R]],
    *,
    concurrency: int,
) -> list[R]:
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(item: T) -> R:
        async with semaphore:
            return await operation(item)

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(run_one(item)) for item in items]

    return [task.result() for task in tasks]
```

This is appropriate for a modest, already-bounded collection. It still creates one task per item. Wrapping 500,000 tasks in a semaphore limits active calls but still allocates 500,000 tasks and leaves a large in-memory waiting room.

> **Rule**: Use a semaphore to bound active work. Use a bounded queue or ingress rejection to bound how much work may wait.

Choose a capacity from a real constraint:

- A provider's concurrency quota.
- The HTTP client's connection-pool limit.
- The database pool size and the share reserved for this operation.
- A measured CPU, memory, or latency budget.

Avoid stacking unrelated limits with inconsistent sizes. A semaphore of 200 in front of a 20-connection HTTP pool merely moves 180 waiters into another queue.

---

## 2. Use a Queue for Large or Streaming Inputs

A fixed worker set plus `asyncio.Queue(maxsize=...)` bounds both task count and buffered work:

```python
import asyncio
from collections.abc import Iterable

Job = str | None

async def fetch(url: str) -> int:
    await asyncio.sleep(0.05)  # Replace with an async client call.
    return 200

async def worker(
    queue: asyncio.Queue[Job],
    results: dict[str, int],
) -> None:
    while True:
        url = await queue.get()
        try:
            if url is None:
                return
            results[url] = await fetch(url)
        finally:
            # One task_done() is required for every successful get().
            queue.task_done()

async def fetch_all(urls: Iterable[str], *, worker_count: int = 10) -> dict[str, int]:
    queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=100)
    results: dict[str, int] = {}

    async with asyncio.TaskGroup() as group:
        for _ in range(worker_count):
            group.create_task(worker(queue, results))

        for url in urls:
            await queue.put(url)  # Suspends when the bounded buffer is full.

        await queue.join()

        # Python 3.11-compatible shutdown: one sentinel per worker.
        for _ in range(worker_count):
            await queue.put(None)

    return results

async def main() -> None:
    results = await fetch_all(
        f"https://service.example/items/{item_id}"
        for item_id in range(50)
    )
    print(len(results))

if __name__ == "__main__":
    asyncio.run(main())
```

Production details:

- Put `task_done()` in `finally`; otherwise one failed item can make `queue.join()` wait forever.
- Decide whether one item failure should fail the whole pipeline, be retried, or become an explicit error result.
- A queue is local to one event loop. It is not durable and does not coordinate other processes or pods.
- Python 3.13+ also has `asyncio.Queue.shutdown()`. Graceful shutdown preserves the `join()` invariant; `shutdown(immediate=True)` deliberately breaks it and should be reserved for abandonment.

If producers must never wait, use `put_nowait()` and turn `QueueFull` into an explicit overload response or drop policy. Silent unbounded buffering is not backpressure.

---

## 3. Treat Timeouts as Budgets

`asyncio.timeout()` limits a whole region:

```python
import asyncio

async def load_page() -> bytes:
    try:
        async with asyncio.timeout(2.0):
            # The budget includes pool/semaphore waiting and the operation itself.
            return await fetch_page()
    except TimeoutError:
        return b"cached fallback"
```

When the deadline expires, the timeout context cancels the **current task**, handles the resulting `CancelledError`, and raises built-in `TimeoutError` outside the context. Cancellation cleanup can take time, so wall-clock duration can exceed the nominal timeout.

Use the event loop's monotonic clock for an absolute deadline:

```python
loop = asyncio.get_running_loop()
deadline = loop.time() + 2.0

async with asyncio.timeout_at(deadline):
    profile = await load_profile()
    recommendations = await load_recommendations(profile)
```

Passing one deadline through nested calls prevents each layer from spending a fresh two seconds:

```python
async def call_downstream(deadline: float) -> bytes:
    async with asyncio.timeout_at(deadline):
        return await receive_response()
```

Keep library-level phase timeouts as well. An HTTP client may need separate connect, pool-acquisition, read, and write limits for useful diagnostics. The outer asyncio deadline is the end-to-end budget; driver timeouts explain which phase exhausted it.

`asyncio.wait_for(awaitable, timeout)` is useful for one awaitable. Like `asyncio.timeout()`, it cancels overdue async work and waits for cancellation to complete. `asyncio.wait(..., timeout=...)` is different: it returns pending tasks without cancelling them.

⚠️ Timing out an await of `asyncio.to_thread()` or an executor future does not stop already-running sync code. The underlying function needs its own timeout or cooperative stop mechanism.

---

## 4. Make Cancellation-Safe Cleanup

Cancellation is normal during timeouts, client disconnects, sibling failure, deployments, and shutdown. Code must preserve resource invariants when it happens:

```python
import asyncio

async def consume() -> None:
    subscription = await open_subscription()
    try:
        await subscription.run()
    except asyncio.CancelledError:
        logger.info("subscription cancelled")
        raise
    finally:
        await subscription.aclose()
```

Rules:

1. Put mandatory cleanup in `finally`.
2. If `CancelledError` is caught, re-raise it after cleanup.
3. Do not catch `BaseException` around ordinary application work.
4. Make cleanup idempotent because partial setup and repeated shutdown signals happen.
5. Bound cleanup too; a drain with no deadline can prevent a deployment from completing.

Do not use broad exception handling as a cancellation policy:

```python
# ❌ Structured-concurrency tools can misbehave because cancellation is swallowed.
try:
    await run_worker()
except BaseException:
    logger.exception("worker failed")
```

`CancelledError` directly inherits from `BaseException`, so `except Exception` does not normally catch it.

---

## 5. Understand What `shield()` Guarantees

`asyncio.shield(task)` prevents cancellation of the **caller** from being forwarded to the inner task. It does not suppress cancellation of the caller:

```python
task = asyncio.create_task(flush_audit_batch())
try:
    await asyncio.shield(task)
except asyncio.CancelledError:
    # The caller is still cancelled. The inner task may still be running.
    register_for_later_observation(task)
    raise
```

Keep a strong reference and eventually retrieve the task's result or exception. The event loop only keeps weak references to tasks.

Shielding is not durability. The process can still crash or be killed, and repeated cancellation can interrupt code that tries to observe the inner task. If work must survive a request or process lifetime, write it transactionally before acknowledging the request or enqueue it in a durable worker system.

Use `shield()` only when the ownership model explains:

- Who retains the task.
- Who observes failure.
- When the task is allowed to finish.
- What happens during process shutdown.

---

## 6. Shut Down in Phases

A graceful service shutdown is a state transition, not one call to `cancel()`:

```text
SIGTERM / framework shutdown
          │
          ▼
stop accepting new work
          │
          ▼
signal producers and consumers
          │
          ▼
drain in-flight work within a deadline
          │
          ▼
cancel remaining tasks
          │
          ▼
close clients, pools, and telemetry
```

A worker loop should have a wait that can be interrupted by its stop signal:

```python
import asyncio

async def worker_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with asyncio.timeout(1.0):
                message = await receive_message()
        except TimeoutError:
            continue  # Re-check stop periodically.
        await handle_message(message)
```

Polling is a compatibility pattern, not the only design. Prefer a broker/client API that accepts cancellation or can be closed to wake blocked receivers.

HTTP frameworks such as Uvicorn normally own signal handling. Standalone Unix services can use `loop.add_signal_handler()`, but it must run in the main thread and is not available on every platform. Keep the application shutdown function independent from how the signal arrives so tests and Windows services can trigger the same path.

`TaskGroup` cancels siblings when a child fails. It does **not** cancel healthy children just because the `async with` body reaches its end; normal exit waits for them. Explicitly signal or cancel long-running tasks before leaving their owning scope.

---

## 7. Keep Blocking Work off the Event Loop

Common event-loop blockers include:

| Blocking work | Better boundary |
|---------------|-----------------|
| Sync HTTP or SDK call | Async client or `asyncio.to_thread()` with a sync timeout |
| `time.sleep()` | `await asyncio.sleep()` |
| Sync database driver | Async driver, or a dedicated thread pool during migration |
| Pure-Python CPU loop | Long-lived process pool or job worker |
| Large sync serialization step | Measure; offload for responsiveness if worthwhile |

Thread offloading preserves loop responsiveness, but it does not create unlimited capacity. `asyncio.to_thread()` uses the loop's default executor, which may also serve framework and library work. A slow dependency can saturate that shared pool and delay unrelated operations.

Use separate, named executors when dependencies need independent capacity budgets. Shut them down at application shutdown, and remember that executor cancellation usually stops only work that has not started.

---

## 8. Observe Saturation, Not Just Average Latency

Useful async service signals include:

- Event-loop delay.
- Active tasks by operation.
- Queue depth, age of oldest item, and rejection count.
- Semaphore or connection-pool wait time.
- In-flight requests per downstream.
- Timeout and cancellation counts by cause.
- Shutdown drain duration and abandoned-work count.
- Thread/process executor queue depth and utilization.

Enable diagnostics in development:

```bash
PYTHONASYNCIODEBUG=1 python app.py
python -X dev app.py
```

```python
loop = asyncio.get_running_loop()
loop.set_debug(True)
loop.slow_callback_duration = 0.05
```

Asyncio slow-callback warnings are not an event-loop-lag metric by themselves. A periodic lag probe detects scheduler delay; task and capacity metrics help explain it.

---

## 9. Production Checklist

- [ ] Fan-out over user or external input has both an active-work limit and a waiting-work policy.
- [ ] End-to-end deadlines include capacity waiting, not only socket time.
- [ ] Driver-level phase timeouts remain enabled.
- [ ] Cancellation cleanup uses `finally` and re-raises `CancelledError`.
- [ ] Detached tasks have an owner, failure observer, and shutdown path.
- [ ] Blocking calls and executor functions have their own termination strategy.
- [ ] Shutdown stops admission before draining work.
- [ ] Local semaphores and queues are not mistaken for cross-process limits.
- [ ] Event-loop delay, queueing, saturation, cancellation, and rejection are measured.

---

## References

- [`asyncio` tasks, cancellation, timeouts, and `TaskGroup`](https://docs.python.org/3/library/asyncio-task.html)
- [`asyncio` queues](https://docs.python.org/3/library/asyncio-queue.html)
- [`asyncio` synchronization primitives](https://docs.python.org/3/library/asyncio-sync.html)
- [`asyncio` event loop](https://docs.python.org/3/library/asyncio-eventloop.html)

---

**Next**: [Context Variables and Request-Scoped State](03_contextvars.md)
