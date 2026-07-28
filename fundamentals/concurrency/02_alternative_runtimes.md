# Subinterpreters and Free-Threaded CPython

> **Who this is for**: Python developers who have read the [decision guide](00_decision_guide.md) and need to recognize when Python 3.14's `InterpreterPoolExecutor` or an optional free-threaded CPython build deserves evaluation. These are advanced deployment choices, not automatic upgrades.

---

## 1. Why These Runtimes Exist

On the regular CPython build, one process has one Global Interpreter Lock (GIL), so ordinary threads do not execute Python bytecode in parallel. Processes solve that by using separate interpreters and memory spaces, but process startup, serialization, and operational isolation have costs.

Modern CPython offers two additional approaches:

| Approach | Multi-core Python? | Isolation model | Deployment requirement |
|----------|--------------------|-----------------|------------------------|
| `InterpreterPoolExecutor` | Yes, one GIL per interpreter | Separate interpreter state in one process | Python 3.14+ |
| Free-threaded CPython | Yes, threads can run Python concurrently | Shared objects and process state | Optional free-threaded build, Python 3.13+ |

They solve different problems. Subinterpreters preserve strong interpreter isolation. Free threading removes the GIL inside one shared object graph.

> **Default**: Start with `ProcessPoolExecutor` for portable CPU parallelism. Adopt an alternative only after compatibility tests and representative benchmarks show a clear benefit.

---

## 2. `InterpreterPoolExecutor` Mental Model

Python 3.14 added `concurrent.futures.InterpreterPoolExecutor`. Each worker:

- Is an OS thread in the current process.
- Owns a separate Python interpreter and GIL.
- Has separate imports, modules, globals, `sys`, `builtins`, and `__main__`.
- Receives callables, arguments, and results through `pickle` serialization.

```text
one OS process
│
├── main interpreter ── main GIL ── submits futures
│
├── worker thread 1 ── interpreter A ── GIL A
├── worker thread 2 ── interpreter B ── GIL B
└── worker thread 3 ── interpreter C ── GIL C
```

Because the GILs are independent, workers can execute Python on different CPU cores. Because the interpreters are isolated, a module import or global assignment in one worker does not update another.

The workers still share the containing process and OS resources. A fatal native crash can take down the whole process, and low-level operations on process resources such as file descriptors can affect other interpreters.

---

## 3. Minimal and Importable Examples

This minimal Python 3.14+ program uses only a picklable built-in callable:

```python
from concurrent.futures import InterpreterPoolExecutor

def main() -> None:
    bases = [2, 3, 5, 7]
    exponents = [100_000, 80_000, 60_000, 50_000]

    with InterpreterPoolExecutor(max_workers=4) as executor:
        results = executor.map(pow, bases, exponents, buffersize=4)
        print([result.bit_length() for result in results])

if __name__ == "__main__":
    main()
```

For application functions, put worker code in an importable module:

```python
# cpu_jobs.py
import hashlib

def hash_rounds(payload: bytes, rounds: int) -> bytes:
    digest = payload
    for _ in range(rounds):
        digest = hashlib.sha256(digest).digest()
    return digest
```

```python
# run_jobs.py
from concurrent.futures import InterpreterPoolExecutor, as_completed

from cpu_jobs import hash_rounds

def main() -> None:
    payloads = [f"job-{index}".encode() for index in range(8)]

    with InterpreterPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(hash_rounds, payload, 200_000): payload
            for payload in payloads
        }
        for future in as_completed(futures):
            payload = futures[future]
            print(payload, future.result().hex()[:12])

if __name__ == "__main__":
    main()
```

The `__main__` guard prevents import-time side effects and keeps the example portable if it is later changed to a process pool.

---

## 4. Isolation and Serialization Costs

Interpreter workers cannot directly share ordinary mutable Python objects. Treat the boundary like a process-pool API:

- Pass small, picklable messages.
- Return small, picklable results.
- Load large read-only data once per worker with `initializer=...` if each interpreter can afford a copy.
- Do not pass clients, sessions, locks, generators, closures, or open files.

Every interpreter imports modules independently. This can multiply module initialization time and memory. It can also surprise code that expects a module global to be initialized once per process.

`Executor.map(..., buffersize=...)` can bound submitted results waiting to be consumed in Python 3.14+. Its `chunksize` argument has no effect for `InterpreterPoolExecutor`; chunking optimization applies only to process pools.

Timeout and cancellation semantics are inherited from executors. `future.result(timeout=...)` stops waiting; it does not stop callable code that is already running. `shutdown(cancel_futures=True)` cancels pending work, not running work.

---

## 5. Dependency Compatibility Is a Gate

Pure Python modules with no process-global assumptions are the easiest candidates. Native extension modules need explicit multiple-interpreter support. Legacy extensions may:

- Refuse to import in an isolated interpreter.
- Share unsafe C-level global state.
- Behave incorrectly or crash when imported by multiple interpreters.

Test the exact production dependency set, including observability agents and transitive native modules. A successful unit test of the worker function is not enough; exercise repeated pool creation, worker initialization, exceptions, and shutdown under load.

Worker exceptions are normally preserved, with an `ExecutionFailed` summary attached as their cause. If the original exception cannot be preserved, the executor may surface `ExecutionFailed` directly. An initializer failure breaks the pool and causes pending submissions to fail.

---

## 6. Free-Threaded CPython Mental Model

A free-threaded CPython build can run with the GIL disabled, allowing multiple threads to execute Python concurrently in one shared process memory space.

Check build and runtime state:

```bash
python -VV
python -c "import sysconfig; print(sysconfig.get_config_var('Py_GIL_DISABLED'))"
python -c "import sys; print(getattr(sys, '_is_gil_enabled', lambda: 'unknown')())"
```

`Py_GIL_DISABLED == 1` means the binary supports free threading. `_is_gil_enabled()` reports whether the GIL is currently active. A free-threaded build can run with the GIL re-enabled, and importing an extension that does not declare free-threading support may re-enable it.

The regular GIL-enabled build remains the standard deployment default. A free-threaded binary is a distinct runtime artifact that must be selected, tested, packaged, and monitored deliberately.

---

## 7. Correctness Changes Before Performance Does

Free threading does not turn compound business operations into atomic operations:

```python
# Still a race: two threads can both observe a missing key.
if key not in cache:
    cache[key] = compute(key)
```

Use the same ownership rules required in any shared-memory parallel program:

- Prefer immutable messages and private state.
- Protect multi-step invariants with locks.
- Use queues to transfer ownership.
- Audit third-party objects for documented thread safety.
- Avoid sharing iterators and stateful clients without explicit guarantees.

Do not infer safety from a built-in operation appearing atomic on one CPython version. Language-level correctness should not depend on undocumented bytecode or GIL behavior.

Thread context inheritance also differs by build in Python 3.14: free-threaded builds default to inheriting a copy of the caller's `contextvars.Context`, while regular builds default to an empty context. Pass `Thread(..., context=...)` explicitly when propagation matters.

---

## 8. Measure the Whole Deployment

Free threading is attractive only when parallel speedup outweighs its costs. Benchmark:

- Single-thread latency and throughput.
- Multi-thread scaling at realistic worker counts.
- Memory consumption.
- Lock contention and queueing.
- Tail latency under mixed workloads.
- Native-extension behavior.
- Whether an extension silently re-enables the GIL.

Thread oversubscription is common when Python threads call libraries that create their own native thread pools. Coordinate Python worker counts with BLAS, OpenMP, ML-runtime, and web-server settings.

For backend deployments, compare at least:

1. Regular CPython with multiple web-worker processes.
2. Regular CPython with a process pool for CPU work.
3. `InterpreterPoolExecutor` on Python 3.14+.
4. A free-threaded build with a proven-compatible dependency set.

Use the simplest model that meets throughput, latency, memory, and failure-isolation requirements.

---

## 9. Decision Table

| Requirement | First choice |
|-------------|--------------|
| Portable CPU parallelism and process isolation | `ProcessPoolExecutor` |
| Python 3.14+, picklable tasks, and interpreter-compatible dependencies | Benchmark `InterpreterPoolExecutor` |
| Shared-memory CPU parallelism with an audited thread-safe codebase | Benchmark free-threaded CPython |
| Native library already provides parallel kernels | Use its supported parallel API and avoid nested oversubscription |
| Durable or horizontally scalable CPU jobs | External worker queue |

---

## References

- [`InterpreterPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html#interpreterpoolexecutor)
- [`concurrent.interpreters`](https://docs.python.org/3/library/concurrent.interpreters.html)
- [Python support for free threading](https://docs.python.org/3/howto/free-threading-python.html)
- [Isolating extension modules](https://docs.python.org/3/howto/isolating-extensions.html)
- [PEP 703 — Making the GIL Optional](https://peps.python.org/pep-0703/)
- [PEP 734 — Multiple Interpreters in the Standard Library](https://peps.python.org/pep-0734/)
- [PEP 779 — Supported Status for Free-Threaded Python](https://peps.python.org/pep-0779/)

---

**Next**: [Asyncio](async/README.md)
