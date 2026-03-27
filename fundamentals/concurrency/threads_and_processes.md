# `ThreadPoolExecutor` & `ProcessPoolExecutor`

Executors from `concurrent.futures` provide a clean, high-level API for running work concurrently or in parallel using pools of workers and `Future`s.

They abstract away low-level thread/process management and expose a simple model:
**submit work → get a Future → collect results**.

- **ThreadPoolExecutor**: multiple *threads* in the same Python process
- **ProcessPoolExecutor**: multiple *processes* (separate Python interpreters)

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

```python
from concurrent.futures import ThreadPoolExecutor

def f(x):
    return x * 2

with ThreadPoolExecutor() as ex:
    for y in ex.map(f, range(10)):
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

Immediate shutdown:

```python
ex.shutdown(wait=False, cancel_futures=True)
```

---

## Choosing `max_workers`

### ThreadPoolExecutor (I/O-bound)

* Often much higher than CPU cores (e.g. 20–200)
* Depends on latency, sockets, and external limits

### ProcessPoolExecutor (CPU-bound)

* Usually close to `os.cpu_count()`
* Too many processes increase context switching and IPC overhead

```python
import os
from concurrent.futures import ProcessPoolExecutor

with ProcessPoolExecutor(max_workers=os.cpu_count()) as ex:
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

* `concurrent.futures.ThreadPoolExecutor`
* `concurrent.futures.ProcessPoolExecutor`
* `concurrent.futures.Future`
* helpers: `as_completed`, `wait`
