# Match Worker Concurrency to What the Task Waits For

> **Who this is for**: Python engineers selecting process, thread, coroutine, or mixed worker pools after choosing a durable transport.

Before reading this, choose where pending work lives in **[Queue and Worker Architectures](04_queue_and_worker_architectures.md)**.

---

## 1. Measure the bottleneck before choosing a pool

A queue is backed up, so the team raises concurrency from 8 to 200. Throughput gets worse because each task performs CPU-heavy parsing and the new workers compete for the same cores and memory. Concurrency only helps when it overlaps the resource a task is waiting for.

Start with the **four workload types** in bold. Duration and scheduling refine the deployment after that.

| Dimension | Question | Design effect |
|---|---|---|
| **CPU-bound** | Is most time spent executing Python/native compute? | Processes or external compute; concurrency near available cores |
| **Blocking I/O-bound** | Does a synchronous client wait on network or disk? | Bounded threads, enough connections, hard provider timeouts |
| **Native async I/O-bound** | Do all hot-path clients support `await`? | Coroutines, semaphore, connection-pool and quota limits |
| **Mixed CPU and I/O** | Do stages have different bottlenecks? | Separate queues and worker deployments |
| Short-running | Is startup/dispatch overhead material? | Batch or keep in process only if loss is acceptable |
| Long-running | Can work exceed shutdown or lease windows? | Heartbeats, checkpoints, deadlines, and draining |
| Periodic | Is creation time part of the requirement? | Scheduler creates an idempotent job |
| Batch/fan-out | Can work split into independent items? | Bound fan-out and aggregate durably |
| Latency-sensitive | Is queue wait part of the SLO? | Reserve capacity and track oldest-message age |
| Resource-intensive | Does one job consume large RAM/GPU/provider quota? | Specialized workers and low concurrency |

Profile before you choose. “It calls an API” is not enough — serialization before the call may dominate. The measurements, with the mechanism for each:

| What you need to know | How to measure it |
|---|---|
| Compute versus wait, per task | `time.thread_time()` beside `time.perf_counter()` inside a worker thread; use `process_time()` only when one isolated process owns the measured task |
| Memory high-water mark | `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` at task end |
| Where the time actually goes | `py-spy top --pid <worker>` against a running worker, no code change or restart |
| Event-loop stalls | asyncio debug mode (`PYTHONASYNCIODEBUG=1`), or a probe coroutine that sleeps 0.1 s in a loop and logs its own overshoot |
| Connection wait | the HTTP/database client's own pool metrics — httpx pool-acquire time, SQLAlchemy pool checkout time |

> **Key insight**: Concurrency is a budget across CPU, memory, sockets, database connections, and provider quotas; the smallest budget sets the useful worker count.

---

## 2. CPU-bound Python normally needs process parallelism

In a normal GIL-enabled CPython build, only one thread executes Python bytecode at a time, so more threads do not scale pure-Python CPU work. The [Python threading documentation](https://docs.python.org/3/library/threading.html) recommends multiprocessing or process pools for CPU-bound work.

Free-threaded CPython builds can disable the GIL, but they are not the default, and an unsupported extension can re-enable it; see the [official free-threading guide](https://docs.python.org/3/howto/free-threading-python.html). Treat free-threading as an explicit tested deployment choice, not an assumption.

Typical CPU work includes image transforms, compression, parsing, simulation, and model inference that does not already manage native parallelism.

Minimal local parallelism — save this as a file and run it:

```python
import zlib
from concurrent.futures import ProcessPoolExecutor

def compress_one(data: bytes) -> int:
    return len(zlib.compress(data, level=9))

# The guard is not optional. Child processes re-import this module, so pool
# construction at module level either raises RuntimeError or spawns recursively.
if __name__ == "__main__":
    payloads = [b"hello world " * 10_000, b"x" * 100_000, bytes(range(256)) * 400]
    with ProcessPoolExecutor(max_workers=4) as pool:
        sizes = list(pool.map(compress_one, payloads))
    assert len(sizes) == len(payloads)
    assert all(0 < size < len(payload) for size, payload in zip(sizes, payloads))
    print(sizes)
```

Expected output is a list of three positive compressed sizes. Exact sizes can vary with the zlib build, so the program asserts the portable contract instead of copying a platform-specific value.

The `if __name__ == "__main__":` guard used to be described as a Windows and macOS concern. As of **Python 3.14 it is required on Linux too**: the default start method on POSIX platforms changed from `fork` to `forkserver` (macOS and Windows already defaulted to `spawn`). Every current default re-imports `__main__` in the child, so a `ProcessPoolExecutor` created at module scope is created again inside each child. Source: [`multiprocessing` documentation](https://docs.python.org/3/library/multiprocessing.html) — "Changed in version 3.14: On POSIX platforms the default start method was changed from *fork* to *forkserver*" (checked 2026-08-03).

Production workers also need:

- Concurrency near the container’s actual CPU limit, not the host’s core count.
- Enough memory for `process_count × per_task_peak_memory` plus the parent process.
- Worker recycling for native-library leaks or fragmentation.
- Graceful draining so shutdown stops new claims before terminating children.
- Separate control of native-library threads (`OMP_NUM_THREADS`, BLAS settings) to avoid process × thread oversubscription.

**How you know the sizing is right**: per-process CPU utilization sits at the cgroup limit and total throughput goes flat — not up — as you add workers. Flat throughput with idle CPU means the bottleneck was never CPU.

⚠️ **The memory sizing has a silent failure mode.** When a child exceeds the container limit the kernel OOM-kills it, and Python raises no exception you can catch — the parent sees a `BrokenProcessPool`, or, in a queue worker, the message simply reappears. So an under-provisioned worker looks like a redelivery problem, not a memory problem. Check the container's OOM-kill count and exit code 137 before debugging the queue.

⚠️ A process worker killed after writing an output but before acknowledging its message causes redelivery. The output write and completion transition must be idempotent.

⚠️ `fork` is no longer the default on any platform as of Python 3.14, so its classic hazard — a child inheriting a network client, lock, or thread from the parent in an unsafe state — now applies only when a project explicitly selects it. The defaults trade it for a different constraint: with `spawn` and `forkserver`, every argument must be picklable and module-level state is re-executed per child, so an import-time database pool is built once per worker process rather than shared. Initialize pools and clients inside each child according to the framework’s process lifecycle either way.

---

## 3. Blocking I/O can use more threads than cores

When a synchronous HTTP or database call waits, CPython releases the GIL around blocking I/O. Other threads can run, so 32 threads may keep a few CPU cores busy while most requests wait on the network.

Minimal thread pool:

```python
import time
from concurrent.futures import ThreadPoolExecutor


def fetch_customer(customer_id: int) -> dict[str, int]:
    time.sleep(0.05)  # Represents a blocking network client.
    return {"customer_id": customer_id}


if __name__ == "__main__":
    customer_ids = [101, 102, 103, 104]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(fetch_customer, customer_ids))
    print(responses)
```

The hardened design bounds every downstream resource:

```python
import httpx
from concurrent.futures import ThreadPoolExecutor


def mock_provider(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"customer_id": request.url.params["customer_id"]})


def fetch_one(client: httpx.Client, customer_id: int) -> dict[str, str]:
    response = client.get(
        "https://provider.example/customer",
        params={"customer_id": customer_id},
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    # Prevents 32 threads from opening an unbounded number of sockets.
    limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
    # Prevents a stuck provider call from occupying a worker thread indefinitely.
    timeout = httpx.Timeout(connect=3.0, read=20.0, write=10.0, pool=2.0)
    transport = httpx.MockTransport(mock_provider)
    items = [101, 102, 103, 104]

    with httpx.Client(limits=limits, timeout=timeout, transport=transport) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda item: fetch_one(client, item), items))
    assert len(responses) == len(items)
    print(responses)
```

**How you know 32 is the right number**: httpx pool-acquire wait sits near zero and the executor's work queue is not growing. If acquire wait climbs, the connection limit — not the thread count — is the binding constraint.

⚠️ The tell for a stuck provider is distinctive and easy to misread: every connection is in use, throughput collapses, and **CPU stays idle**. Adding threads makes it worse, because each new thread only waits on the same exhausted pool. That is a timeout problem, not a concurrency problem.

Threads consume stack memory, add scheduling overhead, share mutable process state, and cannot be safely killed while inside arbitrary library code. Framework time limits do not replace connect/read/total provider timeouts.

Use thread workers when critical clients are synchronous and I/O dominates. Do not wrap CPU-heavy Python in threads merely to keep an event loop responsive; use a process or specialized worker instead.

---

## 4. Native async I/O gives high concurrency with explicit backpressure

Coroutines are effective when the whole hot path is async: queue client, HTTP client, database driver, and storage client. One event loop can hold many waiting operations without one thread per task.

Minimal async fan-out:

```python
import asyncio


async def fetch_one(item: int) -> int:
    await asyncio.sleep(0.05)
    return item * 2


async def main() -> None:
    items = [1, 2, 3, 4]
    results = await asyncio.gather(*(fetch_one(item) for item in items))
    print(results)


asyncio.run(main())
```

`await` only works inside a coroutine (or an async REPL such as `python -m asyncio`), which is why the `gather` call is wrapped rather than written at module level.

That baseline is runnable but creates one coroutine for every input before any semaphore could slow the producer. For an input stream with no small known bound, use a bounded queue and a fixed number of worker coroutines. The queue applies backpressure to the producer; `asyncio.timeout` bounds each complete operation.

```python
import asyncio
import httpx

PER_POD_CONCURRENCY = 40
LOCAL_BUFFER = 80


def mock_provider(request: httpx.Request) -> httpx.Response:
    # Makes the example runnable. Replace MockTransport with the real transport
    # in production; the queue, timeouts, and worker count remain the same.
    return httpx.Response(200, json={"processed": request.url.params["item_id"]})


async def worker(
    queue: asyncio.Queue[int | None],
    client: httpx.AsyncClient,
) -> int:
    processed = 0
    while True:
        item_id = await queue.get()
        try:
            if item_id is None:
                return processed
            async with asyncio.timeout(30):
                response = await client.post(
                    "https://provider.example/process",
                    params={"item_id": item_id},
                )
                response.raise_for_status()
                processed += 1
        finally:
            queue.task_done()


async def produce(queue: asyncio.Queue[int | None], count: int) -> None:
    for item_id in range(count):
        await queue.put(item_id)  # Blocks when 80 payloads are waiting in memory.
    for _ in range(PER_POD_CONCURRENCY):
        await queue.put(None)


async def main() -> None:
    queue: asyncio.Queue[int | None] = asyncio.Queue(maxsize=LOCAL_BUFFER)
    limits = httpx.Limits(max_connections=40, max_keepalive_connections=20)
    timeout = httpx.Timeout(connect=3, read=20, write=10, pool=2)
    transport = httpx.MockTransport(mock_provider)

    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        transport=transport,
    ) as client:
        consumers = [
            asyncio.create_task(worker(queue, client))
            for _ in range(PER_POD_CONCURRENCY)
        ]
        await produce(queue, count=1_000)
        await queue.join()
        counts = await asyncio.gather(*consumers)

    assert sum(counts) == 1_000
    print("processed", sum(counts))


if __name__ == "__main__":
    asyncio.run(main())
```

At most 40 requests and 80 waiting payloads exist locally. The program prints `processed 1000`; if a sentinel is omitted or `task_done()` is skipped, `queue.join()` hangs, which is the failure signal.

`PER_POD_CONCURRENCY=40` is local. Ten pods can create 400 requests. A provider limit of 100 concurrent requests therefore needs a distributed limiter, partitioned token bucket, queue-level dispatch limit, or conservative per-pod budget tied to maximum replicas.

Also bound:

- Database pool size and the fraction reserved for API traffic.
- Provider requests per second and token/byte quotas.
- Queue prefetch or claimed batch size.
- In-memory payload volume, not only coroutine count.
- Retry concurrency so a provider outage does not create a retry storm.

⚠️ One blocking SDK call inside `async def` freezes every coroutine on that event loop. Use an async client, move the call to a bounded thread pool, or isolate it in another worker deployment.

⚠️ Cancellation is cooperative. Code that swallows `CancelledError` or never reaches an `await` can ignore shutdown until the runtime kills it.

Success is visible as high I/O overlap with low event-loop lag, stable connection-pool wait time, and bounded in-flight requests. Rising event-loop lag with low network throughput points to blocking code or CPU work inside the loop.

---

## 5. Mixed workloads need separate queues and deployments

An ingestion pipeline may download documents asynchronously, parse them with CPU-heavy code, then call a blocking legacy service. One universal worker pool forces all stages to share the wrong scaling rule.

```text
documents.io queue ──► async I/O workers
                            │
                            └── enqueue parsed-needed IDs
                                             │
documents.cpu queue ──► prefork/process workers
                                             │
legacy.blocking queue ──► bounded thread workers
```

Separate deployments provide:

- Independent CPU and memory requests.
- Different concurrency and autoscaling signals.
- Separate provider credentials and network policy.
- Failure isolation when one dependency slows down.
- Fair routing so long CPU work does not block short notifications.

Keep payloads small: pass object-store references between stages. Record stage completion before enqueuing the next stage, or use an outbox/workflow engine so a crash cannot lose the handoff.

---

## 6. Framework pool names do not change physics

| Approach | Natural workload | Important boundary |
|---|---|---|
| Celery prefork | CPU-bound and general synchronous tasks | Process memory and child lifecycle |
| Celery threads | Blocking I/O | Some prefork-only features differ; stuck calls need provider timeouts |
| Celery gevent/eventlet | Cooperative I/O with compatible libraries | Monkey-patching and feature differences |
| Dramatiq processes × threads | Synchronous CPU/I/O mixtures | Total concurrency is multiplicative; time limits are best-effort |
| Native async worker | High-concurrency async I/O | You own queue loop, leases, retries, and shutdown |
| Database polling worker | Any pool chosen behind a DB claim | Primary DB load and custom runtime |
| Managed queue poller | Any pool chosen behind a leased message | Visibility renewal and provider limits |
| Local process pool | CPU work inside one service | No durable delivery |
| Local thread pool | Blocking I/O inside one service | No durable delivery |
| In-process background hook | Tiny best-effort work | Lost on restart |

For most Python services, the default subset is **prefork/processes for CPU**, **threads for unavoidable blocking I/O**, and **coroutines for native async I/O**. Greenlet pools are a compatibility choice, not a general upgrade.

Celery’s current [concurrency guide](https://docs.celeryq.dev/en/stable/userguide/concurrency/) recommends prefork as the starting point and notes that alternative pools can disable features such as `soft_timeout` and `max_tasks_per_child`. Framework details live in the [Celery note](frameworks/celery/overview.md).

---

## 7. Scheduling creates work; it does not size the worker pool

A periodic trigger should create or publish one stable, idempotent job:

```text
scheduler firing: 2026-08-03T03:00Z
           │
           └── create job with key nightly-cleanup:2026-08-03
                              │
                              └── queue routes it to the correct worker pool
```

Overlap policy belongs in the job contract: skip, coalesce, serialize, or permit parallel runs. The scheduler must not assume that firing once means executing once.

The framework-neutral contract—including timezones, DST, misfires, catch-up, and one durable firing across replicas—is in [Scheduling and Periodic Work](06_scheduling_and_periodic_work.md). Use APScheduler for timing inside a controlled scheduler process; use Celery Beat or a platform scheduler when already operating that ecosystem; use durable timers in the workflow engine when the timer is part of workflow state.

---

**Next**: [Part 6: Scheduling and Periodic Work](06_scheduling_and_periodic_work.md)
