# Decision Guide: Async, Threads, Processes, and Runtimes

This guide answers the first design question: what concurrency model should this code use?

---

## The one-minute version

| Workload | Best first choice | Why |
|----------|-------------------|-----|
| Many waiting operations using async libraries | `asyncio` | One event loop can coordinate many I/O waits cheaply. |
| Many waiting operations using blocking libraries | `ThreadPoolExecutor` | Threads keep blocking calls off the main flow or event loop. |
| Pure Python CPU-bound work | `ProcessPoolExecutor` | Separate processes run Python bytecode in parallel. |
| C-extension CPU work that releases the GIL | Threads or library-native parallelism | NumPy/PyTorch/OpenCV/zlib-style work may already parallelize. |
| Long-running durable background jobs | Job queue | Retries, persistence, isolation, and horizontal scaling. |
| Per-request context | `contextvars` | Request state follows async tasks safely. |
| Global cross-worker limits | Redis/database/admission service | In-process semaphores are only local. |

One sentence:

> Async is for waiting. Threads are for blocking integration and shared memory. Processes are for CPU parallelism and isolation.

---

## Concurrency vs parallelism

Concurrency means multiple jobs are in progress during the same time period. Parallelism means multiple jobs are executing at the same instant.

`asyncio` gives concurrency, not CPU parallelism. On the default CPython build, threads give concurrency but not parallel execution of Python bytecode. Processes give true CPU parallelism because each process has its own interpreter and GIL.

---

## The GIL

The Global Interpreter Lock means the default CPython interpreter allows only one thread to execute Python bytecode at a time in one process.

This does not make threads useless:

- Blocking I/O releases the GIL while waiting.
- Many C extensions release the GIL around heavy native work.
- Threads are useful for responsiveness and integration with sync libraries.

This also does not make your code correct:

- Compound operations can still race.
- Shared mutable state still needs locks or ownership.
- Free-threaded builds make accidental thread races more visible.

See [01_state_and_safety.md](01_state_and_safety.md) before sharing mutable objects.

---

## Tool-by-tool

### `asyncio`

Use when:

- Libraries are async-native.
- You need many concurrent network calls, DB queries, sleeps, streams, or sockets.
- You need structured cancellation and deadlines.

Avoid when:

- The hot path is CPU-heavy Python.
- You only have blocking libraries and cannot offload them.
- You need durable background execution after the process dies.

Read [async/](async/README.md).

### Threads

Use when:

- You must call blocking sync code from async or request code.
- You need shared memory inside one process.
- You need responsiveness.
- C extensions release the GIL and measurement shows parallel speedup.

Avoid when:

- The workload is pure Python CPU work on the default GIL build.
- Shared mutable state would become complicated.
- The blocking library does not support timeouts.

Read [threads/](threads/README.md).

### Processes

Use when:

- The workload is CPU-heavy pure Python.
- Isolation is valuable.
- The input and output are picklable.
- The work is large enough to amortize serialization overhead.

Avoid when:

- You need to share rich live objects.
- The work is tiny.
- You depend on inherited database clients, sockets, or event loops.

Read [processes/](processes/README.md).

### Subinterpreters

`InterpreterPoolExecutor` is available in modern Python versions and runs isolated interpreters in worker threads. Each interpreter has its own GIL, so it can provide multi-core parallelism without separate OS processes.

The tradeoff is isolation. Mutable Python objects cannot be shared directly between interpreters, and callables, arguments, and results are serialized.

Use it only after you understand process pools and have a reason to prefer subinterpreters. For most backend code, `ProcessPoolExecutor` is still the clearer default for CPU parallelism.

### Free-threaded Python

CPython now has optional free-threaded builds where the GIL can be disabled. The default build still uses the GIL. Free-threaded Python can make threads useful for CPU-bound Python, but it raises the bar for correctness:

- Shared mutable state is genuinely concurrent.
- Some extension modules may not support the free-threaded build yet.
- You need to measure both speedup and single-thread overhead.
- Code must be correct without relying on GIL-shaped behavior.

Check the runtime:

```bash
python -c "import sysconfig; print(sysconfig.get_config_var('Py_GIL_DISABLED'))"
python -c "import sys; print(getattr(sys, '_is_gil_enabled', lambda: 'unknown')())"
```

---

## Design examples

### Fan out to 200 HTTP endpoints

Use async if you have async clients:

```python
async with asyncio.TaskGroup() as tg:
    for url in urls:
        tg.create_task(fetch_bounded(url))
```

Use a semaphore to match the downstream budget. If you only have `requests`, use a thread pool or `asyncio.to_thread()` as a bridge.

### Parse 5 GB of text with Python code

Use a process pool, chunk the input, and pass file paths or byte ranges instead of giant Python objects.

```python
with ProcessPoolExecutor() as ex:
    for result in ex.map(parse_chunk, chunks, chunksize=8):
        handle(result)
```

### Add request IDs to every log line

Use `ContextVar`, set it in middleware, reset it in `finally`, and bind it to your logger. Do not use a module global. Do not use `threading.local()` in async request handlers.

### Limit all pods to 50 calls to an upstream

A local `asyncio.Semaphore(50)` is wrong if you run multiple workers or pods. It gives each process its own budget. Use Redis, a database row, or an admission-control service.

---

## Runtime commands

```bash
# Python version
python -VV

# CPU count
python -c "import os; print(os.cpu_count()); print(getattr(os, 'process_cpu_count', os.cpu_count)())"

# Process start method
python -c "import multiprocessing as mp; print(mp.get_start_method())"
python -c "import multiprocessing as mp; print(mp.get_all_start_methods())"

# GIL/free-threading checks
python -c "import sysconfig; print(sysconfig.get_config_var('Py_GIL_DISABLED'))"
python -c "import sys; print(getattr(sys, '_is_gil_enabled', lambda: 'unknown')())"

# Async debug mode
PYTHONASYNCIODEBUG=1 python app.py
python -X dev app.py
```

---

## Final rule

Pick the simplest model that matches the bottleneck:

- Waiting on I/O with async libraries: `asyncio`.
- Waiting on I/O with blocking libraries: threads.
- Computing in Python: processes.
- Durable work outside a request: a job queue.
- Global coordination: an external system.

Then make it production-grade with bounds, timeouts, cancellation, explicit state ownership, and observability.

---

## References

- [`asyncio`](https://docs.python.org/3/library/asyncio.html)
- [`concurrent.futures`](https://docs.python.org/3/library/concurrent.futures.html)
- [`threading`](https://docs.python.org/3/library/threading.html)
- [`multiprocessing`](https://docs.python.org/3/library/multiprocessing.html)
- [`contextvars`](https://docs.python.org/3/library/contextvars.html)
- [Python free-threading HOWTO](https://docs.python.org/3/howto/free-threading-python.html)
- [PEP 703 - Making the Global Interpreter Lock Optional](https://peps.python.org/pep-0703/)
- [PEP 779 - Criteria for supported status for free-threaded Python](https://peps.python.org/pep-0779/)
