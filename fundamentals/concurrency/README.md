# Concurrency & Parallelism in Python

> A practical guide to `asyncio`, threads, processes, shared state, and the runtime details that matter in production.

[![Python](https://img.shields.io/badge/asyncio-stdlib-3776AB.svg?logo=python&logoColor=white)](https://docs.python.org/3/library/asyncio.html)
[![Python](https://img.shields.io/badge/threading-stdlib-3776AB.svg?logo=python&logoColor=white)](https://docs.python.org/3/library/threading.html)
[![Python](https://img.shields.io/badge/multiprocessing-stdlib-3776AB.svg?logo=python&logoColor=white)](https://docs.python.org/3/library/multiprocessing.html)

---

## What this section teaches

Concurrency is about handling multiple things in progress. Parallelism is about executing multiple things at the same time. Python gives you several tools, and good backend engineers know the tradeoff:

- `asyncio` handles huge numbers of I/O waits with low overhead.
- Threads integrate blocking libraries and share memory inside one process.
- Processes run CPU-heavy Python in parallel and isolate memory.
- Context variables carry request state safely through async call chains.
- Locks, queues, semaphores, and message passing make shared state deliberate.

The goal is not to memorize every API. The goal is to design systems that remain fast, bounded, observable, and correct under load.

---

## Reading order

| Order | Guide | Why it exists |
|------:|-------|---------------|
| 1 | [00_decision_guide.md](00_decision_guide.md) | Choose async, threads, processes, subinterpreters, or job queues. |
| 2 | [01_state_and_safety.md](01_state_and_safety.md) | Understand mutable objects, shared state, thread safety, async safety, and process boundaries. |
| 3 | [async/](async/README.md) | Event loops, coroutines, tasks, `TaskGroup`, timeouts, cancellation, queues, and `contextvars`. |
| 4 | [threads/](threads/README.md) | `ThreadPoolExecutor`, thread primitives, synchronization patterns, blocking I/O, shared memory, and deadlocks. |
| 5 | [processes/](processes/README.md) | `ProcessPoolExecutor`, pickling, process start methods, CPU parallelism, and process-safe sharing. |

---

## Quick chooser

| Problem | First tool to consider | Why |
|---------|------------------------|-----|
| Many HTTP calls with async clients | `asyncio` + `TaskGroup` + semaphore | Low overhead, natural timeouts and cancellation. |
| Many HTTP calls with blocking clients | `ThreadPoolExecutor` or `asyncio.to_thread()` | Blocking calls move off the event loop. |
| Pure Python CPU-heavy loops | `ProcessPoolExecutor` | Separate interpreters avoid the GIL. |
| NumPy, PyTorch, zlib, OpenCV work | Threads can work if the extension releases the GIL | The C extension may run in parallel. Measure it. |
| Per-request logging context | `contextvars.ContextVar` | Isolated per async task, unlike module globals. |
| Cross-process worker jobs | Dramatiq, Celery, RQ, or another queue | Durable retries, isolation, and horizontal scaling. |
| Shared mutable in-memory cache | Threads + locks, or externalize to Redis | Shared memory needs synchronization. |
| Multi-pod global concurrency limit | Redis/database/token service | Local semaphores only protect one process. |

---

## Command cheat sheet

```bash
# Python and runtime
python -VV
python -c "import os; print('cpu_count=', os.cpu_count()); print('process_cpu_count=', getattr(os, 'process_cpu_count', os.cpu_count)())"
python -c "import multiprocessing as mp; print(mp.get_start_method())"

# Free-threaded build checks, available on modern CPython builds
python -c "import sysconfig; print(sysconfig.get_config_var('Py_GIL_DISABLED'))"
python -c "import sys; print(getattr(sys, '_is_gil_enabled', lambda: 'unknown')())"

# Async debugging
PYTHONASYNCIODEBUG=1 python app.py
python -X dev app.py
python -m asyncio

# Unix process checks
ps -o pid,ppid,stat,comm -p <PID>
lsof -p <PID>
kill -TERM <PID>
```

Use optional profilers when the code is slow and you need proof:

```bash
py-spy top --pid <PID>
py-spy dump --pid <PID>
```

---

## Production rules

1. Bound fan-out with a semaphore, queue, or worker pool size.
2. Put a timeout on every external call.
3. Do not block the event loop with sync I/O, `time.sleep()`, or CPU loops.
4. Do not share mutable state unless the sharing model is explicit.
5. Use `contextvars` for request context, not module globals or `threading.local()` in async code.
6. Keep process-pool callables top-level and picklable.
7. Treat cancellation and shutdown as part of the design, not cleanup afterthoughts.
8. Measure under realistic load before claiming a concurrency model is faster.

---

## Next

- [HTTPX Guide](../httpx/README.md) - async HTTP in practice.
- [Safe API Calls](../fastapi/safe_and_scalable_api_calls/README.md) - production concurrency around downstream APIs.
- [Background Work](../../background_work/README.md) - durable work that should not live inside request handlers.
