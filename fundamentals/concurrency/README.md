# Concurrency & Parallelism in Python

> Understanding threads, processes, async, and when to use each.

[![Python](https://img.shields.io/badge/asyncio-stdlib-3776AB.svg?logo=python&logoColor=white)](https://docs.python.org/3/library/asyncio.html)
[![Python](https://img.shields.io/badge/threading-stdlib-3776AB.svg?logo=python&logoColor=white)](https://docs.python.org/3/library/threading.html)
[![Python](https://img.shields.io/badge/multiprocessing-stdlib-3776AB.svg?logo=python&logoColor=white)](https://docs.python.org/3/library/multiprocessing.html)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [threads_vs_processes_vs_async.md](threads_vs_processes_vs_async.md) | Overview | GIL, free-threaded Python (3.14), when to use threads vs processes vs async |
| [threads_and_processes.md](threads_and_processes.md) | Executors | `ThreadPoolExecutor`, `ProcessPoolExecutor`, `InterpreterPoolExecutor`, `submit`, `map`, `as_completed` |
| [async_tutorial.md](async_tutorial.md) | Asyncio | Event loop, coroutines vs tasks, `TaskGroup`, `run_in_executor` |
| [production_patterns.md](production_patterns.md) | Production | Semaphores, queues, timeouts, cancellation, `contextvars`, graceful shutdown |

---

## Reading Order

1. **Threads vs Processes vs Async** — understand the landscape and the GIL
2. **Threads and Processes** — learn the executor APIs
3. **Async Tutorial** — learn asyncio patterns
4. **Production Patterns** — what you need before shipping: bounding, timeouts, cancellation, shutdown

---

## Prerequisites

- Basic Python
- Understanding of I/O-bound vs CPU-bound work

## Next

After this section:

- **[HTTPX Guide](../httpx/README.md)** — async HTTP in practice
- **[Safe API Calls](../fastapi/Safe_and_Scalable_API_calls/README.md)** — production concurrency patterns
