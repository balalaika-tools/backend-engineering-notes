# ThreadPoolExecutor

> **Who this is for**: Python developers integrating blocking libraries or native work into a concurrent backend. Read [State, Mutability, and Safety](../01_state_and_safety.md) before sharing objects between workers.

---

## 1. What a Thread Pool Solves

`ThreadPoolExecutor` runs synchronous callables in reusable OS threads inside the current process. It is the simplest standard-library bridge when a dependency blocks and no async-native API is available.

Threads share process memory, imports, and file descriptors. Communication is cheap, but a crash in native code affects the process and shared mutable objects need deliberate ownership.

Use a thread pool when:

- Work is I/O-bound and uses blocking libraries.
- You need to keep a main thread or event loop responsive.
- You need shared memory inside one process.
- The real work happens in C extensions that release the GIL.

Avoid it when:

- Work is CPU-heavy pure Python on the default CPython build.
- The code mutates shared state without a clear synchronization plan.
- You are using async-native libraries already and can stay on the event loop.

---

## 2. Minimal Blocking-I/O Example

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request

URLS = [
    "https://example.com",
    "https://example.org",
    "https://example.net",
]

def fetch(url: str) -> tuple[str, int]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return url, response.status

def main():
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(fetch, url): url for url in URLS}

        for fut in as_completed(futures):
            url = futures[fut]
            try:
                _, status = fut.result()
            except Exception as exc:
                print(url, "failed:", exc)
            else:
                print(url, status)

if __name__ == "__main__":
    main()
```

---

## 3. Futures Are Result Handles

`executor.submit(fn, *args)` returns a `Future`.

```python
future = executor.submit(fetch, "https://example.com")
result = future.result(timeout=5)
```

Useful methods:

| API | Meaning |
|-----|---------|
| `future.result(timeout=...)` | Return the value or raise the callable's exception. |
| `future.exception(timeout=...)` | Return the exception without raising it. |
| `future.cancel()` | Cancel only if the work has not started. |
| `future.done()` | True when completed, failed, or cancelled. |
| `as_completed(futures)` | Iterate futures in completion order. |
| `wait(futures, return_when=...)` | Wait for a set of futures. |

Always retrieve results from futures. If you never call `result()` or inspect exceptions, failures become easy to miss.

`concurrent.futures.Future` is a blocking/thread-safe result handle. It is not the same type as `asyncio.Future`; do not call its blocking `.result()` method on an event-loop thread.

---

## 4. Choose `map()` or `submit()`

`map()` is concise and returns results in input order:

```python
import os
from concurrent.futures import ThreadPoolExecutor

def stat_file(path: str) -> tuple[str, int]:
    return path, os.stat(path).st_size

paths = ["README.md"]

with ThreadPoolExecutor() as ex:
    for result in ex.map(stat_file, paths):
        print(result)
```

`submit()` plus `as_completed()` gives more control:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def f(x: int) -> int:
    if x == 5:
        raise ValueError("boom")
    return x * 2

with ThreadPoolExecutor() as ex:
    futures = {ex.submit(f, x): x for x in range(10)}

    for fut in as_completed(futures):
        x = futures[fut]
        try:
            print(x, fut.result())
        except Exception as exc:
            print(x, "failed:", exc)
```

Without `buffersize`, `Executor.map()` collects its inputs eagerly. In Python 3.14+, `buffersize` limits how many submitted results may wait ahead of consumption:

```python
with ThreadPoolExecutor(max_workers=20) as ex:
    for result in ex.map(fetch, URLS, buffersize=100):
        print(result)
```

`map()` yields in input order, so one slow early item delays later results that have already finished. Use `submit()` plus `as_completed()` when completion order, per-item metadata, or per-item error handling matters.

---

## 5. Size the Pool from Capacity

Thread pools for I/O can use more workers than CPU cores because most workers wait. CPU count is still relevant for native CPU work and for oversubscription, but downstream capacity is usually the harder bound.

A rough starting estimate is:

```text
needed concurrency ≈ target completions per second × typical operation latency
```

If a call takes 200 ms and the service needs 100 completions per second, roughly 20 calls must be in flight. Cap that estimate by:

- Provider concurrency and rate quotas.
- HTTP/database connection-pool sizes.
- File descriptor, memory, and thread-stack budgets.
- The capacity reserved for other operations in the same process.

Then load-test tail latency and failure behavior. More workers can increase queueing and downstream overload without increasing throughput.

```python
with ThreadPoolExecutor(max_workers=32, thread_name_prefix="http") as ex:
    ...
```

---

## 6. Separate Wait Timeouts from Work Timeouts

Timeout waiting for a result:

```python
from concurrent.futures import ThreadPoolExecutor
import time

def slow() -> str:
    time.sleep(5)
    return "done"

executor = ThreadPoolExecutor(max_workers=1)
future = executor.submit(slow)

try:
    print(future.result(timeout=1))
except TimeoutError:
    # The caller stopped waiting. slow() is probably still running.
    print("timed out; cancelled pending work:", future.cancel())
finally:
    executor.shutdown(wait=False, cancel_futures=True)
```

`Future.result(timeout=...)` limits how long the caller waits. It does not inject a timeout into the blocking function. If the future is already running, `future.cancel()` returns `False`.

The blocking operation needs its own timeout:

```python
import urllib.request

with urllib.request.urlopen(url, timeout=5) as response:
    ...
```

`shutdown(wait=False, cancel_futures=True)` cancels work that has not started and returns without waiting for running work. The Python process still does not exit until executor workers finish. A `with ThreadPoolExecutor(...)` block calls shutdown with `wait=True`, so leaving the block can wait beyond an earlier `Future.result()` timeout.

Python cannot safely kill an arbitrary running thread. Use dependency-level timeouts, cooperative stop events, and short work units. Do not put operations that may block forever in an in-process thread pool.

---

## 7. Call Blocking Code from Asyncio

For a simple blocking I/O call, prefer `asyncio.to_thread()`:

```python
result = await asyncio.to_thread(blocking_io_function, arg1, arg2)
```

`asyncio.to_thread()` propagates the current `contextvars.Context` into the worker thread. That makes it the ergonomic default for request ID and logging context.

Cancelling the asyncio task stops awaiting the result but does not stop a sync function already running in the worker. Capacity remains occupied until that function returns.

Use a custom pool when you need a named, bounded, long-lived pool:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

def read_file(path: str) -> bytes:
    return Path(path).read_bytes()

async def call_blocking(pool: ThreadPoolExecutor, path: str) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool, read_file, path)

async def main() -> None:
    with ThreadPoolExecutor(
        max_workers=8,
        thread_name_prefix="file-io",
    ) as pool:
        print(len(await call_blocking(pool, "README.md")))
```

If you need `contextvars` with a custom executor, copy the context yourself:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

async def read_file_with_context(
    pool: ThreadPoolExecutor,
    path: str,
) -> bytes:
    loop = asyncio.get_running_loop()
    context = copy_context()
    return await loop.run_in_executor(
        pool,
        context.run,
        read_file,
        path,
    )
```

Create a fresh context copy for each concurrent submission. One `Context` cannot be entered concurrently by multiple threads.

---

## 8. Protect Shared State at the Invariant Boundary

Threads share process memory. That means this is unsafe:

```python
cache: dict[str, bytes] = {}

def get_or_compute(key: str) -> bytes:
    if key not in cache:
        cache[key] = compute(key)
    return cache[key]
```

Protect the invariant:

```python
import threading

cache: dict[str, bytes] = {}
cache_lock = threading.Lock()

def get_or_compute(key: str) -> bytes:
    with cache_lock:
        value = cache.get(key)
        if value is None:
            value = compute(key)
            cache[key] = value
        return value
```

If `compute()` is slow, avoid holding the lock while it runs. Use a more careful single-flight pattern or move the cache to Redis.

---

## 9. Avoid Pool and Lock Deadlocks

The classic thread-pool deadlock: a worker waits for another future from the same saturated pool.

```python
from concurrent.futures import ThreadPoolExecutor

def outer(ex: ThreadPoolExecutor) -> int:
    inner = ex.submit(lambda: 42)
    return inner.result()

with ThreadPoolExecutor(max_workers=1) as ex:
    print(ex.submit(outer, ex).result())  # deadlock
```

Rules:

- Do not block a worker waiting for work submitted to the same small pool.
- Do not hold locks while calling `future.result()`.
- Use separate pools for separate blocking domains when needed.
- Prefer queues and worker ownership for pipelines.

---

## 10. Distinguish Thread-Local and Context-Local State

`threading.local()` gives each OS thread its own storage:

```python
local = threading.local()
local.request_id = "req-123"
```

That is useful in threaded sync code. It is wrong for async request context because many asyncio tasks share the same OS thread.

Use `contextvars.ContextVar` for request context. See [../async/03_contextvars.md](../async/03_contextvars.md).

---

## 11. Common Failure Modes

- Using threads for pure Python CPU speedups on the default GIL build.
- Mutating globals from multiple threads without locks.
- Creating a new pool per request.
- Forgetting timeouts inside blocking network calls.
- Waiting on futures from inside the same saturated pool.
- Using `queue.Queue.get()` directly on the event loop.
- Assuming code that "worked under the GIL" is safe on free-threaded Python.
- Assuming a `Future` timeout stopped the underlying call.
- Letting one blocking dependency saturate the default executor used by unrelated code.
- Relying on daemon or executor threads to complete critical work during process exit.

---

## References

- [`concurrent.futures.ThreadPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor)
- [`threading`](https://docs.python.org/3/library/threading.html)
- [`queue.Queue`](https://docs.python.org/3/library/queue.html)
- [`asyncio.to_thread`](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread)

---

**Next**: [Thread Synchronization Primitives and Patterns](02_synchronization_primitives.md)
