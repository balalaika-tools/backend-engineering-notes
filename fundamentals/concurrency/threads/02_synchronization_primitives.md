# Thread Synchronization Primitives and Patterns

> **Who this is for**: Developers who need to protect shared in-process state or coordinate raw threads. Prefer [ThreadPoolExecutor](01_thread_pool_executor.md) for independent calls; use these primitives when workers genuinely share an invariant or lifecycle.

The backend default is still simple: prefer ownership transfer through queues, immutable messages, one clear lock around shared state, or an external system such as Redis/PostgreSQL. Reach for clever synchronization only when the simpler design has a measured problem.

---

## 1. Thread Lifecycle and Failure

A raw `threading.Thread` has a small lifecycle:

| Phase | Meaning |
|-------|---------|
| Created | The `Thread` object exists, but `start()` has not been called. |
| Started | The OS thread exists and may run the target function. |
| Running or waiting | The thread is executing Python, blocked on I/O, waiting for a lock, or sleeping. |
| Finished | The target returned or raised. `join()` can observe completion. |

```python
import threading
import time

def poll(stop: threading.Event) -> None:
    while not stop.is_set():
        print("poll once")
        stop.wait(0.5)

stop = threading.Event()
thread = threading.Thread(target=poll, args=(stop,), name="poller")

thread.start()
time.sleep(2)
stop.set()
thread.join(timeout=5)
if thread.is_alive():
    raise TimeoutError("poller did not stop within 5 seconds")
```

Rules:

- Use `join()` during shutdown when the work matters.
- Prefer cooperative stop signals such as `threading.Event`.
- Do not rely on daemon threads for important cleanup. Daemon threads can be abandoned when the process exits.
- Prefer `ThreadPoolExecutor` for request fan-out or many short blocking calls.
- `join(timeout=...)` always returns `None`; call `is_alive()` to learn whether the timeout expired.

An exception in a raw thread does not propagate through `join()`. Python sends it to `threading.excepthook()`, which prints it by default. If the caller needs a result or failure, prefer a `Future`, send a result through a queue, or install an explicit exception-reporting policy.

---

## 2. Mutex: `threading.Lock`

A mutex lets only one thread enter a critical section at a time. In Python, the usual mutex is `threading.Lock`.

```python
import threading

lock = threading.Lock()
counter = 0

def increment() -> None:
    global counter
    with lock:
        counter += 1
```

Use a lock to protect an invariant, not just an individual line of code. For example, "the dict and the count agree" is an invariant.

Keep lock scopes small, but not so small that the protected operation stops being atomic at the business-logic level.

---

## 3. Try-Lock

A try-lock attempts to acquire a lock without waiting forever. In Python, use `acquire(blocking=False)` or `acquire(timeout=...)`.

```python
if lock.acquire(blocking=False):
    try:
        update_shared_state()
    finally:
        lock.release()
else:
    skip_or_retry_later()
```

With a timeout:

```python
if not lock.acquire(timeout=0.2):
    raise TimeoutError("could not acquire cache lock")

try:
    update_shared_state()
finally:
    lock.release()
```

Try-locks are useful for best-effort maintenance work, avoiding shutdown hangs, and detecting contention. They are a poor substitute for a clear lock order when correctness depends on acquiring multiple locks.

---

## 4. Reentrant Lock: `threading.RLock`

`RLock` allows the same thread to acquire the same lock more than once. It must release it the same number of times.

```python
import threading

class Account:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._balance = 0

    def deposit(self, amount: int) -> None:
        with self._lock:
            self._change(amount)

    def _change(self, amount: int) -> None:
        with self._lock:
            self._balance += amount
```

Use `RLock` when a public method and a helper both need the same lock. Avoid invoking unknown callbacks under the lock; re-entrancy can hide a confused ownership design. If you do not need re-entry, prefer `Lock` because accidental nested locking remains visible.

---

## 5. Coarse Versus Fine-Grained Locks

A coarse lock protects a large area of state with one lock. It is simple and often correct enough.

```python
import threading

class CoarseCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        with self._lock:
            return self._data.get(key)
```

A fine-grained design uses multiple locks, often one per shard or resource. It can reduce contention, but it raises the cost of correctness.

```python
import threading

class ShardedCache:
    def __init__(self, shards: int = 16) -> None:
        self._locks = [threading.Lock() for _ in range(shards)]
        self._data: list[dict[str, bytes]] = [{} for _ in range(shards)]

    def _index(self, key: str) -> int:
        return hash(key) % len(self._data)

    def get(self, key: str) -> bytes | None:
        index = self._index(key)
        with self._locks[index]:
            return self._data[index].get(key)
```

Prefer coarse locks until measurement shows contention. Use fine locks only when you can describe which lock protects which invariant and how multiple locks are ordered.

---

## 6. Semaphores Protect Capacity

A semaphore limits how many threads can enter a section at once. It protects capacity, not ownership of one invariant.

```python
import threading

api_slots = threading.BoundedSemaphore(10)

def call_api() -> bytes:
    with api_slots:
        return blocking_http_call()
```

Use `BoundedSemaphore` when over-release would indicate a bug. Common uses:

- Limit concurrent calls to a downstream service.
- Match a thread workload to a database connection pool.
- Bound access to scarce local resources.

For async code, use `asyncio.Semaphore` instead.

---

## 7. Condition Variables Protect Predicates

A condition variable lets threads wait until a predicate becomes true. It combines a lock with a wait/notify mechanism.

```python
import threading

condition = threading.Condition()
state = {"ready": False}

def wait_until_ready() -> None:
    with condition:
        condition.wait_for(lambda: state["ready"])

def mark_ready() -> None:
    with condition:
        state["ready"] = True
        condition.notify_all()
```

Rules:

- Always wait for a predicate, not just a notification.
- Use `wait_for(...)` or a `while not predicate: condition.wait()` loop.
- Call `notify()` or `notify_all()` while holding the condition lock.
- Prefer `queue.Queue` for normal producer-consumer handoff.
- Add a timeout or shutdown predicate when a waiter must not block forever.

---

## 8. Events Are Level-Triggered Signals

Use `threading.Event` for one-way readiness or shutdown signals.

```python
import threading

stop = threading.Event()

def worker() -> None:
    while not stop.is_set():
        do_one_unit()
        stop.wait(0.1)

def shutdown() -> None:
    stop.set()
```

Choose the signal shape by intent:

| Intent | Primitive |
|--------|-----------|
| One-way ready/stop flag | `threading.Event` |
| Predicate changed under a lock | `threading.Condition` |
| Transfer work with backpressure | `queue.Queue` |
| Limit resource capacity | `threading.Semaphore` |

An event stores a boolean flag, not a count. Calling `set()` five times before a waiter calls `wait()` still represents one signalled state. Use a queue when every occurrence must be delivered.

---

## 9. Blocking Queue and Producer-Consumer

`queue.Queue` is Python's standard blocking queue for threads. `put()` blocks when the queue is full. `get()` blocks when it is empty.

```python
import queue
import threading

WorkItem = str | None
work: queue.Queue[WorkItem] = queue.Queue(maxsize=100)

def process(url: str) -> None:
    print("processing", url)

def producer(urls: list[str]) -> None:
    for url in urls:
        work.put(url)
    work.put(None)

def consumer() -> None:
    while True:
        item = work.get()
        try:
            if item is None:
                return
            process(item)
        finally:
            work.task_done()

threading.Thread(target=consumer).start()
producer(["https://example.com"])
work.join()
```

Key points:

- `maxsize` is backpressure.
- Call `task_done()` once for every successful `get()`.
- `join()` waits until all queued tasks are marked done.
- With multiple consumers, send one sentinel per consumer or use another shutdown signal.
- Use `Queue(maxsize=...)` when producers must experience backpressure; `SimpleQueue` is unbounded.

Python 3.13+ adds `Queue.shutdown()`. Graceful shutdown rejects new `put()` calls while allowing consumers to drain existing items. `shutdown(immediate=True)` abandons queued work and can unblock `join()` without the usual "every item was processed" guarantee.

---

## 10. Put Shared State Behind One API

Put shared mutable state behind one API and keep the lock private.

```python
import threading

class ThreadSafeCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: bytes) -> None:
        with self._lock:
            self._data[key] = value
```

For get-or-compute caches, decide deliberately:

- Compute inside the lock: simple, but all callers wait.
- Compute outside the lock: better concurrency, but duplicate work is possible.
- Use a single-flight pattern: more complex, but only one thread computes each key.
- Externalize to Redis or a database when the cache must work across processes or pods.

---

## 11. Reader-Writer Pattern

A reader-writer lock lets multiple readers enter together while writers need exclusive access. Python's standard library does not provide a reader-writer lock.

For most backend code, a normal `Lock` is simpler and usually fast enough. Consider a reader-writer pattern only for read-heavy shared state where measurements show the single lock is a bottleneck.

A minimal sketch uses `Condition`:

```python
import contextlib
import threading
from collections.abc import Iterator

class ReadWriteLock:
    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False

    @contextlib.contextmanager
    def read_lock(self) -> Iterator[None]:
        with self._condition:
            self._condition.wait_for(lambda: not self._writer)
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextlib.contextmanager
    def write_lock(self) -> Iterator[None]:
        with self._condition:
            self._condition.wait_for(
                lambda: not self._writer and self._readers == 0
            )
            self._writer = True
        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()
```

This sketch is reader-biased and can starve writers under heavy read load. Production reader-writer locks need a fairness policy.

---

## 12. Race Conditions Protect Invariants, Not Lines

A race condition happens when correctness depends on timing between concurrent operations.

```python
if key not in cache:
    cache[key] = compute(key)
```

Two threads can both observe the missing key and both compute. The fix is to protect the whole invariant with a lock, transfer ownership through a queue, or use an external atomic operation.

---

## 13. Deadlock

A deadlock happens when threads wait forever for each other.

The classic shape is inconsistent lock ordering:

```python
# Thread A: acquire user_lock, then account_lock
# Thread B: acquire account_lock, then user_lock
```

Rules:

- Give locks a total order and acquire them in that order everywhere.
- Do not call unknown callbacks while holding a lock.
- Do not wait on a future while holding a lock needed by that future.
- Use timeouts for shutdown paths and diagnostics, not as the main correctness strategy.

---

## 14. Livelock

A livelock is not blocked, but it still makes no progress. Threads keep reacting to each other.

For example, two workers can repeatedly acquire one lock, fail to acquire the second with a try-lock, release the first, and retry in sync forever.

Fixes:

- Use one consistent lock order.
- Add jittered backoff when retrying try-lock loops.
- Prefer a queue or a single owner when retries become complicated.
- Put a retry budget on best-effort work.

---

## 15. Compare-And-Swap and Optimistic Concurrency

**Compare-And-Swap (CAS)** atomically replaces a value only if it still equals an expected old value. It is used to build lock-free data structures and optimistic concurrency systems. In normal Python backend code:

- The standard library does not give you a general-purpose CAS for Python objects.
- The GIL is not a substitute for CAS.
- Free-threaded Python makes accidental shared-state assumptions more dangerous, not less.
- Prefer `Lock`, `Queue`, or external atomic operations.

CAS-like patterns are common outside Python memory:

```sql
UPDATE documents
SET body = :new_body, version = version + 1
WHERE id = :id AND version = :expected_version;
```

If the update count is zero, another writer won the race and the caller should reload or report a conflict.

---

## 16. Quick Map

| Concept | Python shape |
|---------|--------------|
| Thread lifecycle | `Thread(...).start()`, cooperative stop signal, `join()` |
| Thread pool | `concurrent.futures.ThreadPoolExecutor` |
| Mutex | `threading.Lock` |
| Reentrant lock | `threading.RLock` |
| Try-lock | `lock.acquire(blocking=False)` or `lock.acquire(timeout=...)` |
| Semaphore | `threading.Semaphore`, `threading.BoundedSemaphore` |
| Condition variable | `threading.Condition` |
| Signaling | `threading.Event`, `Condition.notify_all()` |
| Blocking queue | `queue.Queue` |
| Producer-consumer | `queue.Queue(maxsize=...)` plus worker threads |
| Start together / phase rendezvous | `threading.Barrier` |
| Reader-writer | Custom/library primitive; no stdlib `RWLock` |
| CAS | No general stdlib API; use locks or external atomic operations |

---

## 17. Design Checklist

- Can I avoid sharing this mutable object?
- Can a queue transfer ownership instead of exposing shared state?
- Is one coarse lock enough?
- If I need multiple locks, is the acquisition order documented and followed?
- Is every blocking wait bounded or shutdown-aware?
- Am I using thread primitives only in threaded code, not directly on the event loop?
- Does this state need to work across processes or pods?

---

## References

- [`threading`](https://docs.python.org/3/library/threading.html)
- [`queue`](https://docs.python.org/3/library/queue.html)
- [`concurrent.futures`](https://docs.python.org/3/library/concurrent.futures.html)

---

**Next**: [Processes and CPU Parallelism](../processes/README.md)
