# ProcessPoolExecutor

> **Who this is for**: Python developers moving substantial, independent CPU work out of a web or orchestration process. Read [State, Mutability, and Safety](../01_state_and_safety.md) before designing process-shared state.

---

## 1. What a Process Pool Solves

`ProcessPoolExecutor` runs callables in separate Python processes. Each worker has its own interpreter, GIL, and memory space, so pure-Python CPU work can execute on multiple cores on the regular CPython build.

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

## 2. Minimal CPU Example

```python
from concurrent.futures import ProcessPoolExecutor, as_completed
import math

def heavy(n: int) -> float:
    total = 0.0
    for i in range(1, n):
        total += math.sqrt(i) * math.sin(i)
    return total

def main() -> None:
    inputs = [300_000, 350_000, 400_000, 450_000]

    with ProcessPoolExecutor() as ex:
        futures = [ex.submit(heavy, n) for n in inputs]
        for fut in as_completed(futures):
            print(fut.result())

if __name__ == "__main__":
    main()
```

The `__main__` guard prevents child imports from starting another pool. It is required for safe importing with the `spawn` and `forkserver` start methods, which cover every platform's modern default. Process pools also do not work reliably from an interactive interpreter because worker processes must import the main module.

---

## 3. Design an Importable, Picklable Boundary

Everything sent to a process must be serializable with `pickle`.

Good:

- Top-level functions.
- Built-in scalar values.
- Lists, tuples, dicts, and dataclasses made of picklable values.
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

Importability is separate from serialization. A top-level function can still fail if its module performs side effects during import, and a serializable object can still be too large to transfer efficiently.

---

## 4. Choose `submit()` or `map()`

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

Without `buffersize`, `Executor.map()` collects its inputs eagerly. `buffersize` bounds how far submission can run ahead of result consumption; `chunksize` controls how many input elements share each process work item. Tune them independently.

---

## 5. Size Workers for CPU and Memory

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

## 6. Know the Process Start Method

Check the start method:

```bash
python -c "import multiprocessing as mp; print(mp.get_start_method())"
```

Common methods:

| Method | Meaning | Notes |
|--------|---------|-------|
| `spawn` | Start a fresh Python interpreter | Default on Windows and macOS. Safest, higher startup cost. |
| `forkserver` | A server process forks clean worker processes | Default in Python 3.14+ on POSIX platforms that support it, such as Linux. |
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

Libraries should let their caller provide a multiprocessing context rather than setting one global start method. Locks and other multiprocessing objects created by one context may be incompatible with processes from another.

The `spawn` and `forkserver` methods also mean worker imports rerun module top-level code. Keep pool construction, servers, telemetry startup, and other side effects behind an application entry point.

---

## 7. Initialize and Recycle Workers Deliberately

Use `initializer` for read-only worker-local setup that would otherwise repeat for every task:

```python
from concurrent.futures import ProcessPoolExecutor

model = None

def initialize_worker(model_path: str) -> None:
    global model
    model = load_model(model_path)

def predict(record: bytes) -> list[float]:
    if model is None:
        raise RuntimeError("worker model is not initialized")
    return model.predict(record)

def run_predictions(records: list[bytes]) -> list[list[float]]:
    with ProcessPoolExecutor(
        initializer=initialize_worker,
        initargs=("models/current.bin",),
    ) as executor:
        return list(executor.map(predict, records))
```

Each worker loads its own copy, so include that memory in capacity planning. An initializer failure breaks the pool and pending work raises `BrokenProcessPool`.

`max_tasks_per_child` can replace workers periodically when unavoidable native leaks or fragmentation accumulate:

```python
with ProcessPoolExecutor(max_tasks_per_child=1_000) as executor:
    ...
```

Recycling adds startup latency. When no explicit context is supplied, using this option selects `spawn`; it is incompatible with `fork`.

---

## 8. Separate Cancellation from Termination

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

These terminate **all living workers in that executor**, not one future. Abrupt termination can skip `finally` blocks and leave external writes, locks, pipes, or queues inconsistent. After either call, the pool cannot accept new work.

Use them only when graceful shutdown has failed or continuing is less safe than abandoning work. Design ordinary shutdown around cooperative completion, small task boundaries, and idempotent outputs.

`future.result(timeout=...)` and `future.cancel()` do not terminate a call that has started. They only bound the caller's wait or cancel pending work.

---

## 9. Prefer Messages to Shared Process State

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

Shared-memory segments require explicit synchronization and lifecycle cleanup. Close every handle and arrange one owner to call `unlink()`. A killed process can leave tracked OS resources behind until the resource tracker or an operator cleans them up.

---

## 10. Own the Pool Outside Async Request Handlers

Create the pool once in the application entry point or framework lifespan, then pass the pool to code that submits work:

```python
import asyncio
from concurrent.futures import ProcessPoolExecutor

def cpu_work(x: int) -> int:
    return sum(range(10_000_000)) + x

async def cpu_work_async(pool: ProcessPoolExecutor, x: int) -> int:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool, cpu_work, x)

async def main() -> None:
    with ProcessPoolExecutor() as pool:
        results = await asyncio.gather(
            cpu_work_async(pool, 1),
            cpu_work_async(pool, 2),
        )
        print(results)

if __name__ == "__main__":
    asyncio.run(main())
```

Do not construct a process pool at module import in code that workers import. With `spawn` or `forkserver`, every worker imports the module and would create its own unused executor object.

Cancelling `cpu_work_async()` stops the asyncio task from waiting; it does not reliably stop `cpu_work()` after a process has begun executing it. Bound process submissions and make output commits idempotent.

Do not pass request-scoped `ContextVar` state implicitly. Process boundaries require explicit serialization:

```python
def cpu_work_with_context(request_id: str, x: int) -> int:
    ...

request_id = request_id_var.get()
await loop.run_in_executor(pool, cpu_work_with_context, request_id, x)
```

---

## 11. Failure Modes and Observability

- Creating a new process pool inside every request.
- Passing a lambda or nested function.
- Passing a database session or HTTP client to a worker.
- Sending huge objects when a path, ID, or blob reference would do.
- Using a process pool for tiny work where IPC dominates.
- Depending on module globals that differ per worker process.
- Forgetting that local semaphores and counters are per process, not global.
- Assuming a future timeout stopped CPU work already running.
- Constructing a pool during module import.
- Ignoring `BrokenProcessPool` after a worker crashes or an initializer fails.
- Force-terminating workers while they hold IPC locks or write non-idempotent output.

Measure:

- Submission queue depth and age.
- Task runtime and serialization time.
- Worker CPU and resident memory.
- Pool startup and worker-recycle latency.
- `BrokenProcessPool`, timeout, and forced-termination counts.
- End-to-end backlog, not just worker execution time.

---

## 12. References

- [`concurrent.futures.ProcessPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html#processpoolexecutor)
- [`multiprocessing` start methods](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods)
- [`multiprocessing.shared_memory`](https://docs.python.org/3/library/multiprocessing.shared_memory.html)

---

**Next**: [Background Work and Durable Jobs](../../../background_work/README.md)
