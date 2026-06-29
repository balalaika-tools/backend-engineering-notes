# ProcessPoolExecutor

`ProcessPoolExecutor` runs callables in separate Python processes. It is the cleanest stdlib tool for CPU-bound Python work when you want true parallelism on the default CPython build.

---

## When to use it

Use a process pool when:

- The work is CPU-bound.
- The work is pure Python or does not release the GIL.
- Each task is large enough to justify process scheduling and serialization.
- Inputs and outputs are picklable.
- You want isolation from the main process.

Avoid it when:

- The work is mostly network or database I/O.
- Each task is tiny and called millions of times individually.
- The callable needs live sockets, database sessions, open files, or closures.
- You need low-latency access to a large mutable object.

---

## Minimal example

```python
from concurrent.futures import ProcessPoolExecutor, as_completed
import math

def heavy(n: int) -> float:
    total = 0.0
    for i in range(1, n):
        total += math.sqrt(i) * math.sin(i)
    return total

def main():
    inputs = [300_000, 350_000, 400_000, 450_000]

    with ProcessPoolExecutor() as ex:
        futures = [ex.submit(heavy, n) for n in inputs]
        for fut in as_completed(futures):
            print(fut.result())

if __name__ == "__main__":
    main()
```

The `__main__` guard matters. On Windows and macOS it is required because child processes import your module. On modern POSIX Python, it is still the habit you want because defaults have moved away from raw `fork`.

---

## Pickling rules

Everything sent to a process must be serializable with `pickle`.

Good:

- Top-level functions.
- Built-in scalar values.
- Lists, tuples, dicts, dataclasses, and Pydantic models made of picklable values.
- Small bytes payloads.

Bad:

- Lambdas.
- Nested functions.
- Closures over live state.
- Generators.
- Open file handles.
- Database connections.
- HTTP clients.
- Locks.
- Event loops.

```python
# Bad: nested function is not importable by child processes.
def main():
    def work(x: int) -> int:
        return x * x

    with ProcessPoolExecutor() as ex:
        print(list(ex.map(work, range(10))))
```

Move the function to module top level:

```python
def work(x: int) -> int:
    return x * x

def main():
    with ProcessPoolExecutor() as ex:
        print(list(ex.map(work, range(10))))
```

---

## `submit()` vs `map()`

Use `submit()` when each task needs its own error handling, timeout, retry, or metadata:

```python
from concurrent.futures import ProcessPoolExecutor, as_completed

with ProcessPoolExecutor() as ex:
    futures = {ex.submit(work, item): item for item in items}

    for fut in as_completed(futures):
        item = futures[fut]
        try:
            print(item, fut.result())
        except Exception as exc:
            print(item, "failed:", exc)
```

Use `map()` when you have many inputs and want results in input order:

```python
with ProcessPoolExecutor() as ex:
    for result in ex.map(work, items, chunksize=500):
        print(result)
```

`chunksize` matters for process pools. It groups many small inputs into fewer process messages. Too small wastes time on IPC. Too large delays first results and can imbalance work.

In Python 3.14+, `Executor.map(..., buffersize=...)` can limit how many submitted tasks are waiting ahead of consumption:

```python
with ProcessPoolExecutor() as ex:
    for result in ex.map(work, items, chunksize=500, buffersize=20):
        print(result)
```

---

## Choosing worker count

For CPU-heavy Python, start near the CPU count:

```python
import os
from concurrent.futures import ProcessPoolExecutor

workers = getattr(os, "process_cpu_count", os.cpu_count)() or 1

with ProcessPoolExecutor(max_workers=workers) as ex:
    ...
```

Use fewer workers when each worker needs a lot of memory or when the machine is already running database, web, or background services. More processes are not always faster.

---

## Start methods

Check the start method:

```bash
python -c "import multiprocessing as mp; print(mp.get_start_method())"
```

Common methods:

| Method | Meaning | Notes |
|--------|---------|-------|
| `spawn` | Start a fresh Python interpreter | Default on Windows and macOS. Safest, higher startup cost. |
| `forkserver` | A server process forks clean worker processes | Default on POSIX in Python 3.14+. Safer than raw `fork` in multi-threaded programs. |
| `fork` | Fork the current process | Fast, but risky with threads and inherited state. |

Prefer the platform default unless you have measured a real need and understand the consequences.

If you must choose explicitly:

```python
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor

ctx = mp.get_context("spawn")

with ProcessPoolExecutor(mp_context=ctx) as ex:
    ...
```

---

## Shutdown and emergency stop

Normal shutdown:

```python
pool.shutdown(wait=True)
```

Cancel futures that have not started:

```python
pool.shutdown(wait=False, cancel_futures=True)
```

This does not stop work already running in a child process. In Python 3.14+, `ProcessPoolExecutor` also exposes emergency process controls:

```python
pool.terminate_workers()
pool.kill_workers()
```

Use those only when graceful shutdown has failed or the work is no longer safe to continue. Design ordinary application shutdown around cooperative completion or cancellation.

---

## Sharing state between processes

Processes do not share normal Python objects. This is a feature.

Options:

| Tool | Use for | Tradeoff |
|------|---------|----------|
| Return values | Simple result collection | Best default. |
| `multiprocessing.Queue` | Producer/consumer IPC | Good handoff, not durable. |
| `multiprocessing.Value` / `Array` | Small shared counters or arrays | Needs synchronization. |
| `multiprocessing.shared_memory` | Large numeric buffers | Fast, but you manage layout and locks. |
| `multiprocessing.Manager()` | Shared dict/list-like proxies | Easy, much slower. |
| Redis/database | Cross-process and cross-host state | Operational dependency, but production-friendly. |

For backend systems, external state is usually the correct production answer. In-memory process sharing is best for local CPU pipelines, not global app coordination.

---

## Process pools from async code

Create the pool once, not per request:

```python
import asyncio
from concurrent.futures import ProcessPoolExecutor

pool = ProcessPoolExecutor()

def cpu_work(x: int) -> int:
    return sum(range(10_000_000)) + x

async def cpu_work_async(x: int) -> int:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool, cpu_work, x)
```

Shut it down during application shutdown:

```python
pool.shutdown(wait=True)
```

Do not pass request-scoped `ContextVar` state implicitly. Process boundaries require explicit serialization:

```python
def cpu_work(request_id: str, x: int) -> int:
    ...

request_id = request_id_var.get()
await loop.run_in_executor(pool, cpu_work, request_id, x)
```

---

## Common pitfalls

- Creating a new process pool inside every request.
- Passing a lambda or nested function.
- Passing a database session or HTTP client to a worker.
- Sending huge objects when a path, ID, or blob reference would do.
- Using a process pool for tiny work where IPC dominates.
- Depending on module globals that differ per worker process.
- Forgetting that local semaphores and counters are per process, not global.

---

## References

- [`concurrent.futures.ProcessPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html#processpoolexecutor)
- [`multiprocessing` start methods](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods)
- [`multiprocessing.shared_memory`](https://docs.python.org/3/library/multiprocessing.shared_memory.html)
