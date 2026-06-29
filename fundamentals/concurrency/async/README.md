# Asyncio

> Asyncio is the right tool when work spends most of its time waiting on network, database, timers, or other non-blocking I/O.

---

## Read these

| Guide | Topic |
|-------|-------|
| [01_event_loop_and_tasks.md](01_event_loop_and_tasks.md) | Event loop mental model, coroutines, tasks, `TaskGroup`, `async with`, executor offloading. |
| [02_production_patterns.md](02_production_patterns.md) | Semaphores, queues, timeouts, cancellation, graceful shutdown, event-loop blockers. |
| [03_contextvars.md](03_contextvars.md) | Request-scoped state, logging context, and propagation across tasks, threads, and workers. |

---

## Mental model

The event loop is a single-threaded coordinator. It runs one task until that task reaches an `await`, then it can run another ready task.

Async code scales when tasks spend most of their time awaiting I/O:

```python
async with asyncio.TaskGroup() as tg:
    for url in urls:
        tg.create_task(fetch(url))
```

Async code stalls when a task blocks the loop:

```python
time.sleep(1)       # blocks every task on the loop
requests.get(url)   # blocks every task on the loop
cpu_heavy_loop()    # blocks every task on the loop
```

Use async-native libraries on the hot path. Offload blocking work with `asyncio.to_thread()` for blocking I/O, or a process pool for CPU-heavy Python.

---

## Commands

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
- Bound fan-out with `asyncio.Semaphore` or `asyncio.Queue(maxsize=...)`.
- Put timeouts around external calls with `asyncio.timeout()`.
- Let `CancelledError` propagate after cleanup.
- Use `contextvars` for request metadata.
- Never use blocking sync clients on the event loop.
- Track event-loop lag in staging and production.
