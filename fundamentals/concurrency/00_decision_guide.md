# Choosing Asyncio, Threads, Processes, and Runtimes

> **Who this is for**: Python backend developers choosing an execution model before they write concurrency code. This guide assumes normal synchronous Python knowledge but no prior concurrency model.

---

## 1. Start with the Bottleneck

Concurrency is useful only when it matches what prevents work from finishing:

| Work is mostly... | First choice | Why |
|-------------------|--------------|-----|
| Waiting through async-native clients | `asyncio` | One event loop coordinates many suspended I/O operations cheaply. |
| Waiting through blocking clients | `ThreadPoolExecutor` or `asyncio.to_thread()` | Blocking calls run away from the main flow or event loop. |
| Executing pure Python on CPU | `ProcessPoolExecutor` | Separate interpreters execute Python on multiple cores on regular CPython. |
| Executing native code that releases the GIL | Library-native parallelism or threads | Native work may already run in parallel; measure and avoid oversubscription. |
| Running after request/process failure | Durable job queue | Persistence, retries, isolation, and horizontal scaling are lifecycle requirements. |
| Coordinating all workers or pods | External store or admission service | In-process locks, queues, and semaphores protect only one process. |

> **Default mental model**: Asyncio coordinates async waits. Threads integrate blocking waits. Processes isolate and parallelize Python CPU work.

This is a starting point, not a guarantee of speed. Serialization, queueing, memory, downstream capacity, and cancellation behavior often decide the final design.

---

## 2. Concurrency Is Not Parallelism

**Concurrency** means several operations are in progress during the same period. **Parallelism** means operations execute at the same instant.

```text
concurrent, one worker:
job A  [run][ wait........ ][run]
job B       [run][ wait ][run]

parallel, two workers:
job A  [run....................]
job B  [run....................]
```

Asyncio normally gives concurrency on one event-loop thread. Threads give concurrency and can run native code in parallel, but the GIL-enabled CPython build allows only one thread at a time to execute Python bytecode in one interpreter. Processes give parallelism by using separate interpreters and memory spaces.

Parallelism does not automatically reduce request latency. A small CPU task can become slower after process startup, serialization, scheduling, and result transfer. Benchmark the whole operation.

---

## 3. Use This Decision Flow

```text
Must the work survive this process?
├── yes ──> durable broker / job worker
└── no
    │
    ├── mostly waiting?
    │   ├── async-native dependency ──> asyncio
    │   └── blocking dependency ──────> threads / to_thread
    │
    └── mostly computing?
        ├── native library releases GIL ──> its API or threads; benchmark
        └── Python bytecode
            ├── regular CPython ──────────> processes
            └── controlled modern runtime
                ├── Python 3.14+ isolated interpreters ─> benchmark InterpreterPoolExecutor
                └── audited free-threaded build ───────> benchmark threads
```

Then ask:

1. What capacity limits active work?
2. How much work may wait in memory?
3. What is the end-to-end deadline?
4. Can running work actually be cancelled?
5. Which state is shared, and who owns it?
6. Does the limit apply per task, process, pod, or deployment?

If those answers are missing, the execution model is not production-ready.

---

## 4. Understand the GIL Boundary

On the regular CPython build, the **Global Interpreter Lock** allows one thread at a time to execute Python bytecode in one interpreter.

Threads remain useful because:

- Blocking socket and file operations release the GIL while waiting.
- Many native extensions release the GIL around expensive kernels.
- A thread can keep the event loop or UI responsive while sync code waits.

The GIL is not a correctness guarantee:

```python
if account.balance >= amount:
    account.balance -= amount
```

The read, decision, and write form one business invariant. Another thread can interleave, and an async task can interleave if an await is introduced inside a larger operation. Use explicit ownership, locks, transactions, or atomic external operations.

Optional free-threaded CPython builds can disable the GIL, and Python 3.14 adds `InterpreterPoolExecutor` with one GIL per isolated interpreter. Both require compatibility and performance validation; see [Subinterpreters and Free-Threaded CPython](02_alternative_runtimes.md).

---

## 5. Compare the Execution Models

| Model | Shares Python objects? | CPU-parallel Python? | Startup/dispatch cost | Failure isolation | Main risk |
|-------|------------------------|----------------------|-----------------------|-------------------|-----------|
| Asyncio tasks | Yes, one process | No | Low | Low | Blocking the loop or unbounded fan-out |
| Threads | Yes, one process | Not on regular CPython | Low to moderate | Low | Races, deadlocks, and calls that cannot be stopped |
| Processes | No by default | Yes | High | Stronger | Pickling, memory, startup, and stale per-process state |
| Subinterpreters | No mutable object sharing by default | Yes | Must be measured | Same OS process | Dependency compatibility and isolated module state |
| Free-threaded threads | Yes | Yes | Thread-level | Low | True shared-memory races and extension compatibility |
| Job workers | Message-based | Depends on worker model | Network/broker overhead | Strong and durable | Delivery semantics, idempotency, and operations |

Choose the simplest row whose boundaries match the problem. Shared memory is convenient until it makes ownership unclear; isolation is expensive until it prevents an entire class of bugs.

---

## 6. Know What Timeouts and Cancellation Mean

The tools do not offer the same stop guarantee:

| Tool | A timeout/cancel stops waiting? | Stops work already running? |
|------|---------------------------------|-----------------------------|
| Asyncio task | Yes | Requests cooperative cancellation at an await point |
| Thread future | Yes | No; Python cannot safely kill an arbitrary running thread |
| Process future | Yes | Not through `Future.cancel()`; Python 3.14+ can terminate the whole pool's workers |
| Subinterpreter future | Yes | No general per-call forced stop |
| External job | Depends on API | Requires worker/job-specific cooperative or forceful termination |

A client-side timeout is not a termination mechanism. For threads, use library timeouts and cooperative stop signals. For processes, decide whether abandoning, recycling, or forcefully terminating workers is safe. For remote work, propagate deadlines and design idempotent cancellation.

---

## 7. Apply the Choice to Backend Scenarios

### Fan out to several downstream APIs

- Use asyncio when the clients are async-native.
- Use a thread pool when a required SDK is blocking.
- Bound active calls to downstream capacity.
- Bound queued work separately.
- Use one end-to-end deadline plus client phase timeouts.

See [Async Production Patterns](async/02_production_patterns.md).

### Parse a large dataset with Python code

- Partition into chunks large enough to amortize serialization.
- Pass file paths, byte ranges, or compact records instead of a giant object graph.
- Start with a process pool near the CPU allocation, then measure.
- Limit workers when every process holds a large model or dataset.

See [ProcessPoolExecutor](processes/01_process_pool_executor.md).

### Call a blocking vendor SDK from FastAPI

- Put the call in `asyncio.to_thread()` for simple integration.
- Use a dedicated `ThreadPoolExecutor` when the SDK needs its own capacity budget.
- Configure SDK-level connect/read timeouts.
- Remember that cancelling the HTTP request does not stop a running SDK call.

See [ThreadPoolExecutor](threads/01_thread_pool_executor.md).

### Limit an upstream to 50 calls across all pods

An `asyncio.Semaphore(50)` gives each event loop its own 50 permits. With four worker processes in five pods, the theoretical deployment limit becomes 1,000.

Use Redis, a database, a gateway, or a dedicated admission service for a deployment-wide budget. Define failure behavior when that coordinator is unavailable.

### Carry a request ID into logging

Use a `ContextVar`, bind it at the request boundary, and reset the token in `finally`. Pass it explicitly across process and broker boundaries. Do not put a mutable database session into ambient context and assume task propagation makes concurrent use safe.

See [Context Variables](async/03_contextvars.md).

---

## 8. Detect the Runtime You Actually Have

```bash
# Version and build
python -VV

# CPU allocation visible to this process
python -c "import os; print(getattr(os, 'process_cpu_count', os.cpu_count)())"

# Multiprocessing defaults on this platform
python -c "import multiprocessing as mp; print(mp.get_start_method())"
python -c "import multiprocessing as mp; print(mp.get_all_start_methods())"

# Free-threading build support and current GIL state
python -c "import sysconfig; print(sysconfig.get_config_var('Py_GIL_DISABLED'))"
python -c "import sys; print(getattr(sys, '_is_gil_enabled', lambda: 'unknown')())"
```

Do not size a pool from host CPU count alone in containers. `os.process_cpu_count()` reflects the CPUs usable by the current process on supported Python versions and is the default used by modern `ProcessPoolExecutor`.

---

## 9. Final Design Checklist

- Is the bottleneck waiting, Python computation, native computation, or durability?
- Are required libraries async-compatible, thread-safe, process-safe, and interpreter-safe?
- What bounds active work and waiting work?
- Do timeouts include time spent waiting for capacity?
- Can started work be stopped, or only abandoned?
- What happens to exceptions from every task or future?
- Is mutable state private, synchronized, transactionally protected, or externalized?
- Does every pool/client have one clear lifecycle and shutdown owner?
- Does the design still work with multiple workers and pods?
- Have latency, throughput, memory, and failure behavior been measured under realistic load?

---

## References

- [`asyncio`](https://docs.python.org/3/library/asyncio.html)
- [`concurrent.futures`](https://docs.python.org/3/library/concurrent.futures.html)
- [`threading`](https://docs.python.org/3/library/threading.html)
- [`multiprocessing`](https://docs.python.org/3/library/multiprocessing.html)
- [Python support for free threading](https://docs.python.org/3/howto/free-threading-python.html)

---

**Next**: [State, Mutability, and Safety](01_state_and_safety.md)
