# Asyncio

> Use asyncio to coordinate many operations that spend most of their lifetime waiting on async-compatible I/O.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://docs.python.org/3/library/asyncio.html)

---

## Contents

| Guide | Topic | Why it matters |
|-------|-------|----------------|
| [Event Loops, Coroutines, and Tasks](01_event_loop_and_tasks.md) | Awaitables, tasks, `TaskGroup`, `gather()`, exception groups, and sync boundaries | Builds the execution and ownership model first. |
| [Production Patterns](02_production_patterns.md) | Admission control, queues, deadlines, cancellation, shielding, shutdown, and observability | Keeps async systems bounded and recoverable under load. |
| [Context Variables](03_contextvars.md) | Request metadata and propagation across tasks, threads, executors, and processes | Prevents per-request state from leaking or hiding unsafe resources. |

---

## Mental model

The event loop is a cooperative scheduler. It runs a task until the task completes, raises, or awaits an operation that is not ready. It can then run another ready task.

Async code scales when tasks spend most of their time awaiting I/O:

```python
async with asyncio.TaskGroup() as tg:
    for url in urls:
        tg.create_task(fetch(url))
```

Async code stalls when a task blocks the loop:

```python
time.sleep(1)       # Blocks every task on this loop.
requests.get(url)   # A sync client blocks every task on this loop.
cpu_heavy_loop()    # No await means no cooperative scheduling.
```

Use async-native libraries on the hot path. Offload blocking work with `asyncio.to_thread()` for blocking I/O, or a process pool for CPU-heavy Python.

---

## Reading Order

1. **Event loop and tasks** — learn what runs, when it yields, and who owns child work.
2. **Production patterns** — add capacity bounds, time budgets, cancellation, and shutdown.
3. **Context variables** — propagate small request metadata without creating hidden shared resources.

---

## Diagnostic Commands

```bash
# Start the asyncio REPL
python -m asyncio

# Enable asyncio debug mode
PYTHONASYNCIODEBUG=1 python app.py

# Enable broader Python development diagnostics
python -X dev app.py
```

In code:

```python
loop = asyncio.get_running_loop()
loop.set_debug(True)
loop.slow_callback_duration = 0.05
```

---

## Production checklist

- Use `TaskGroup` for structured fan-out.
- Use semaphores for active-operation capacity and bounded queues for waiting-work capacity.
- Put timeouts around external calls with `asyncio.timeout()`.
- Let `CancelledError` propagate after cleanup.
- Use `contextvars` for request metadata.
- Never use blocking sync clients on the event loop.
- Track event-loop delay, queue depth, capacity wait, timeouts, and cancellations.

---

**Next**: [Event Loops, Coroutines, and Tasks](01_event_loop_and_tasks.md)
