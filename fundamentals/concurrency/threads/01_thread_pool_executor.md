# ThreadPoolExecutor

`ThreadPoolExecutor` runs callables in a pool of OS threads inside the current Python process. It is the simplest stdlib tool for integrating blocking I/O with a concurrent program.

---

## When to use it

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

## Minimal example

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
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = [ex.submit(fetch, url) for url in URLS]

        for fut in as_completed(futures):
            url, status = fut.result()
            print(url, status)

if __name__ == "__main__":
    main()
```

---

## Futures

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

---

## `map()` vs `submit()`

`map()` is concise and returns results in input order:

```python
from concurrent.futures import ThreadPoolExecutor

def parse(blob: bytes) -> str:
    return blob.decode().upper()

with ThreadPoolExecutor() as ex:
    for result in ex.map(parse, blobs):
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

In Python 3.14+, `Executor.map(..., buffersize=...)` can limit how many unfinished tasks are submitted ahead of result consumption:

```python
with ThreadPoolExecutor(max_workers=20) as ex:
    for result in ex.map(fetch, urls, buffersize=100):
        print(result)
```

---

## Choosing `max_workers`

Thread pools for I/O often use more workers than CPU cores because most workers are waiting.

Start with:

- 5-20 for local disk or small downstream systems.
- 20-100 for network I/O with real timeouts.
- Provider or database connection limits as the hard ceiling.

The default `ThreadPoolExecutor` size is intentionally modest. Production systems should set the value explicitly based on downstream limits and measurement.

```python
with ThreadPoolExecutor(max_workers=32, thread_name_prefix="http") as ex:
    ...
```

---

## Timeouts, cancellation, and shutdown

Timeout waiting for a result:

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import time

def slow():
    time.sleep(5)
    return "done"

with ThreadPoolExecutor() as ex:
    fut = ex.submit(slow)

    try:
        print(fut.result(timeout=1))
    except TimeoutError:
        print("timed out")
```

Cancel pending work during shutdown:

```python
ex.shutdown(wait=False, cancel_futures=True)
```

This does not stop a function that is already running. Python cannot safely kill an arbitrary thread. Design blocking functions with their own timeouts and cooperative stop checks.

---

## From async code

For a simple blocking I/O call, prefer `asyncio.to_thread()`:

```python
result = await asyncio.to_thread(blocking_io_function, arg1, arg2)
```

`asyncio.to_thread()` propagates the current `contextvars.Context` into the worker thread. That makes it the ergonomic default for request ID and logging context.

Use a custom pool when you need a named, bounded, long-lived pool:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

pool = ThreadPoolExecutor(max_workers=20, thread_name_prefix="blocking-http")

async def call_blocking(url: str) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool, fetch_bytes, url)
```

If you need `contextvars` with a custom executor, copy the context yourself:

```python
from contextvars import copy_context

ctx = copy_context()
return await loop.run_in_executor(pool, ctx.run, fetch_bytes, url)
```

---

## Shared state

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

## Deadlocks

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

## Thread-local vs context-local

`threading.local()` gives each OS thread its own storage:

```python
local = threading.local()
local.request_id = "req-123"
```

That is useful in threaded sync code. It is wrong for async request context because many asyncio tasks share the same OS thread.

Use `contextvars.ContextVar` for request context. See [../async/03_contextvars.md](../async/03_contextvars.md).

---

## Common pitfalls

- Using threads for pure Python CPU speedups on the default GIL build.
- Mutating globals from multiple threads without locks.
- Creating a new pool per request.
- Forgetting timeouts inside blocking network calls.
- Waiting on futures from inside the same saturated pool.
- Using `queue.Queue.get()` directly on the event loop.
- Assuming code that "worked under the GIL" is safe on free-threaded Python.

---

## References

- [`concurrent.futures.ThreadPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor)
- [`threading`](https://docs.python.org/3/library/threading.html)
- [`queue.Queue`](https://docs.python.org/3/library/queue.html)
- [`asyncio.to_thread`](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread)
