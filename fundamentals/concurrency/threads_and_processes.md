# `ThreadPoolExecutor` & `ProcessPoolExecutor`

Executors from `concurrent.futures` provide a clean, high-level API for running work concurrently or in parallel using pools of workers and `Future`s.

They abstract away low-level thread/process management and expose a simple model:
**submit work → get a Future → collect results**.

- **ThreadPoolExecutor**: multiple *threads* in the same Python process
- **ProcessPoolExecutor**: multiple *processes* (separate Python interpreters)
- **InterpreterPoolExecutor** (Python 3.14+): multiple isolated interpreters in one process, each with its own GIL

---

## TL;DR — which one to use

✅ **ThreadPoolExecutor** when:
- Your workload is **I/O-bound** (HTTP calls, DB queries, disk I/O, APIs)
- Threads spend most of their time waiting
- You want shared memory and low startup overhead

✅ **ProcessPoolExecutor** when:
- Your workload is **CPU-bound** (numerical loops, parsing, compression, ML preprocessing)
- You need **true parallelism** (bypasses the GIL)
- You accept process startup + serialization overhead

---

## Core concepts

### Futures

- `executor.submit(fn, *args)` → returns a `Future`
- `future.result(timeout=...)` → get the result or raise the exception
- `future.exception()` → inspect failure without raising
- `as_completed(futures)` → iterate as tasks finish (completion order)

---

### GIL (one-liner)

- Threads cannot execute Python bytecode in parallel for CPU-bound work (GIL)
- Processes run in separate interpreters, enabling real CPU parallelism

---

## Quick examples

### ThreadPoolExecutor — I/O-bound (HTTP)

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request

URLS = [
    "https://example.com",
    "https://example.org",
    "https://example.net",
]

def fetch(url: str) -> tuple[str, int]:
    with urllib.request.urlopen(url, timeout=10) as r:
        return url, r.status

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

### ProcessPoolExecutor — CPU-bound

```python
from concurrent.futures import ProcessPoolExecutor, as_completed
import math

def heavy(n: int) -> float:
    s = 0.0
    for i in range(1, n):
        s += math.sqrt(i) * math.sin(i)
    return s

def main():
    nums = [300_000, 350_000, 400_000, 450_000]

    with ProcessPoolExecutor() as ex:
        futures = [ex.submit(heavy, n) for n in nums]
        for fut in as_completed(futures):
            print(fut.result())

if __name__ == "__main__":
    main()
```

---

## `map()` vs `submit()`

### `executor.map()`

* Best for "many inputs → many outputs"
* Results are returned **in input order**
* Minimal boilerplate
* Limited per-task control
* In Python 3.14+, `buffersize=...` can limit how many unfinished tasks are submitted ahead of consumption

```python
from concurrent.futures import ThreadPoolExecutor

def f(x):
    return x * 2

with ThreadPoolExecutor() as ex:
    for y in ex.map(f, range(10)):
        print(y)
```

```python
with ThreadPoolExecutor(max_workers=20) as ex:
    for y in ex.map(f, range(10_000), buffersize=100):
        print(y)
```

---

### `submit()` + `as_completed()`

* Results are returned **in completion order**
* Better per-task exception handling
* Supports timeouts, cancellation, and retries

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def f(x):
    if x == 5:
        raise ValueError("boom")
    return x * 2

with ThreadPoolExecutor() as ex:
    futures = {ex.submit(f, x): x for x in range(10)}
    for fut in as_completed(futures):
        x = futures[fut]
        try:
            print(x, "->", fut.result())
        except Exception as e:
            print(x, "failed:", e)
```

---

## Ordered output with full control (index-based pattern)

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def f(i: int, x: int) -> tuple[int, int]:
    return i, x * 2

xs = [10, 20, 30, 40]

with ThreadPoolExecutor() as ex:
    futures = [
        ex.submit(f, i, x)
        for i, x in enumerate(xs)
    ]

    results = [None] * len(xs)

    for fut in as_completed(futures):
        i, value = fut.result()
        results[i] = value

print(results)
```

---

## Timeouts, cancellation, shutdown

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError

def slow():
    import time
    time.sleep(5)
    return "done"

with ThreadPoolExecutor() as ex:
    fut = ex.submit(slow)

    try:
        print(fut.result(timeout=1))
    except TimeoutError:
        print("Timed out, cancel attempt:", fut.cancel())
```

Stop accepting new work and cancel jobs that have not started yet:

```python
ex.shutdown(wait=False, cancel_futures=True)
```

This does not kill work that is already running. It only cancels pending futures.

---

## Choosing `max_workers`

### ThreadPoolExecutor (I/O-bound)

* Often much higher than CPU cores (e.g. 20–200)
* Depends on latency, sockets, and external limits
* The default is conservative: `min(32, (os.process_cpu_count() or 1) + 4)` in Python 3.13+

### ProcessPoolExecutor (CPU-bound)

* Usually close to `os.process_cpu_count()` (or `os.cpu_count()` on older Python)
* Too many processes increase context switching and IPC overhead

```python
import os
from concurrent.futures import ProcessPoolExecutor

workers = os.process_cpu_count() or 1

with ProcessPoolExecutor(max_workers=workers) as ex:
    ...
```

---

## ProcessPool gotchas (pickling rules)

Everything sent to a process must be **picklable**:

* Top-level functions
* Simple data structures

Not allowed:

* Lambdas
* Nested functions
* Generators
* Open file handles or sockets

---

## Windows & macOS note: `__main__` guard

```python
def main():
    ...

if __name__ == "__main__":
    main()
```

This guard is also a good habit on Linux. In Python 3.14, the default start method on POSIX changed from `fork` to `forkserver` (macOS and Windows already defaulted to `spawn`), so code that accidentally depended on fork-specific inheritance should pass an explicit `mp_context`.

```python
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

ctx = multiprocessing.get_context("fork")

with ProcessPoolExecutor(mp_context=ctx) as ex:
    ...
```

Prefer the default start method unless you have measured a real need for `fork`.

---

## Python 3.14+: `InterpreterPoolExecutor`

`InterpreterPoolExecutor` sits between threads and processes:

* Workers are threads, but each runs in its own isolated Python interpreter
* Each interpreter has its own GIL, so pure Python code can use multiple cores
* Mutable objects cannot be shared directly between interpreters
* Callables, arguments, and return values are serialized with `pickle`

It can be useful when you want CPU parallelism without separate processes, but `ProcessPoolExecutor` remains the simpler default until the subinterpreter ecosystem is mature.

---

## Useful patterns

### Thread-based pipeline (I/O-heavy)

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def download(i: int) -> bytes:
    return f"data-{i}".encode()

def parse(blob: bytes) -> str:
    return blob.decode().upper()

ids = range(100)

with ThreadPoolExecutor(max_workers=50) as ex:
    downloads = [ex.submit(download, i) for i in ids]
    parses = []

    for fut in as_completed(downloads):
        parses.append(ex.submit(parse, fut.result()))

    results = [f.result() for f in as_completed(parses)]
```

---

### `chunksize` with ProcessPool `map()`

```python
from concurrent.futures import ProcessPoolExecutor

def f(x):
    return x * x

xs = range(1_000_000)

with ProcessPoolExecutor() as ex:
    results = ex.map(f, xs, chunksize=5_000)
    print(next(iter(results)))
```

---

## Common pitfalls

### ThreadPoolExecutor

* CPU-heavy loops do not scale due to the GIL
* Shared mutable state without locks causes race conditions

### ProcessPoolExecutor

* Large objects lead to slow serialization
* Lambdas and closures cause runtime errors
* Debugging and logging are harder

---

## Best practices checklist

* Use threads for I/O-bound workloads
* Use processes for CPU-bound workloads
* Always use the `__main__` guard with ProcessPoolExecutor
* Handle exceptions per `Future`
* Add timeouts to external calls
* Batch or chunk small CPU tasks

---

## Decision table

| Workload                      | Best choice         | Why                        |
| ----------------------------- | ------------------- | -------------------------- |
| HTTP / DB / file I/O          | ThreadPoolExecutor  | low overhead, waiting time |
| CPU-heavy computation         | ProcessPoolExecutor | real parallelism           |
| Mixed pipeline                | Threads + Processes | stage separation           |
| Shared in-memory cache        | Threads             | same address space         |
| Isolation / fault containment | Processes           | separate memory            |

---

## References

* [`concurrent.futures` docs](https://docs.python.org/3/library/concurrent.futures.html)
* [`ThreadPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html#threadpoolexecutor)
* [`ProcessPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html#processpoolexecutor)
* [`InterpreterPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html#interpreterpoolexecutor)
* Helpers: `as_completed`, `wait`
