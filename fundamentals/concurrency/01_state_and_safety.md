# State, Mutability, and Safety

> **Who this is for**: Developers who know Python objects and need to reason about which state can safely cross task, thread, process, or deployment boundaries. Read the [decision guide](00_decision_guide.md) first.

---

## 1. Start with Reachability and Ownership

The most important concurrency question is not "which primitive do I use?" It is:

> **Who can reach this state, who may mutate it, and which scheduler can run those owners?**

Python variables are names bound to objects. Mutability belongs to the object, not the variable name:

```python
items: list[int] = []
alias = items
alias.append(1)
assert items == [1]
```

If two concurrent owners can reach `items`, both names lead to the same mutable object. Type annotations and local variable syntax do not make it private.

---

## 2. Vocabulary

| Term | Meaning |
|------|---------|
| Mutable | The object can be changed in place. Examples: `list`, `dict`, `set`, most class instances. |
| Immutable | The object cannot be changed in place. Examples: `int`, `float`, `str`, `bytes`, `tuple` when it contains only immutable values. |
| Shared | More than one task, thread, process, request, or module can reach the same object. |
| Thread-safe | Multiple OS threads can use it concurrently without corrupting state or breaking invariants. |
| Async-safe | It does not corrupt logical per-task state and does not block the event loop. |
| Process-safe | Multiple processes can use the state through a defined serialization, IPC, shared-memory, or external coordination protocol without breaking invariants. |

Safety has at least two axes:

1. **Memory correctness** — the runtime and object do not become corrupted.
2. **Application correctness** — multi-step business invariants remain true.

A container can protect its internal memory and still be unsafe for a check-then-act sequence. "Thread-safe dict operation" does not make "read balance, validate, then update balance" atomic.

---

## 3. State-Sharing Matrix

| State location or object | Mutable? | Shared across async tasks? | Shared across threads? | Shared across processes? | Safe by default? | What to do |
|--------------------------|----------|----------------------------|------------------------|--------------------------|------------------|------------|
| Local immutable value (`int`, `str`) | No | Only if passed/stored elsewhere | Only if passed/stored elsewhere | Copied or serialized | Usually yes | Prefer these for request data. |
| Local mutable value (`list`, `dict`) | Yes | No, unless passed to another task | No, unless passed to another thread | No, unless sent/shared | Safe only while private | Do not hand it to concurrent workers unless ownership is clear. |
| Module global immutable config | No | Yes, one per process | Yes, one per process | No, each process has its own copy | Usually yes | Good for read-only settings. |
| Module global mutable object | Yes | Yes | Yes | No, except inherited/copy-on-write details | No | Avoid, or protect with the right lock/queue. |
| Class variable mutable object | Yes | Yes if class is shared | Yes if class is shared | No by default | No | Avoid mutable class-level defaults. |
| Instance attribute | Often | If same instance is shared | If same instance is shared | No by default | Depends | Treat a shared instance like shared mutable state. |
| Mutable default argument | Yes | Yes through the function object | Yes through the function object | Per process | No | Use `None` then create a new object inside. |
| Closure over mutable object | Yes | Yes if function is shared | Yes if function is shared | Per process | No | Same risk as a global, just hidden. |
| `threading.local()` | Yes | Broken for async tasks on same thread | Isolated per thread | No | Thread-only | Use for thread-local data, not request state in async servers. |
| `contextvars.ContextVar` | The value can be mutable | Binding is isolated by context/task | Propagation depends on API and runtime settings | No | Good for request metadata | Store small immutable IDs; a copied binding may still point to one mutable object. |
| `asyncio.Lock`, `Semaphore`, `Queue` | Internal state | Yes, within one event loop | Not thread-safe | No | Async-safe only | Use among tasks on the same event loop. |
| `threading.Lock`, `RLock`, `Event` | Internal state | Blocks event loop if used directly | Yes | No | Thread-safe | Use in threaded sync code; avoid holding across `await`. |
| `queue.Queue` | Yes | Blocks event loop if used directly | Yes | No | Thread-safe | Use between threads. Use `asyncio.Queue` between tasks. |
| `multiprocessing.Queue` | Yes | Not an async primitive | Usable, but process-oriented | Yes | Process-safe IPC | Use between processes; consider backpressure. |
| `multiprocessing.Value` / `Array` | Yes | Not async-oriented | Can be used with care | Yes | Only with synchronization | Use the lock or provide your own. |
| `multiprocessing.Manager()` proxies | Yes | Not async-oriented | Can be used with care | Yes | Safer but slower | Good for simple coordination, not hot paths. |
| `multiprocessing.shared_memory` | Yes | Not async-oriented | Can be used with care | Yes | No | Design your own synchronization and layout. |
| Redis/database/object storage | Yes | Yes via clients | Yes via clients | Yes | Depends on operation | Use atomic operations, transactions, or locks. |

---

## 4. Async Safety Is Not Thread Safety

Async tasks usually run on one thread, so they do not execute Python bytecode at exactly the same instant on the default event loop. They can still interleave at every `await`.

```python
counter = 0

async def increment():
    global counter
    value = counter
    await asyncio.sleep(0)
    counter = value + 1
```

Two tasks can both read `0`, both pause, then both write `1`. The code is single-threaded and still wrong.

Use an `asyncio.Lock` when tasks share mutable state:

```python
import asyncio

counter = 0
lock = asyncio.Lock()

async def increment():
    global counter
    async with lock:
        counter += 1
```

An `asyncio.Lock` may be held across `await` when the whole async operation is the invariant it protects. Keep that region short and do not perform unrelated or unbounded I/O while holding it.

Do not use `threading.Lock` as your default async lock. It blocks the event loop if acquisition waits, and it does not model ownership between tasks. Asyncio primitives are not thread-safe and are intended for tasks on one event loop.

---

## 5. Match the Primitive to the Scheduler

Thread-safe objects often use blocking locks. Blocking is fine in synchronous threaded code and dangerous on an event loop.

```python
from queue import Queue

q = Queue()

async def handler():
    item = q.get()  # blocks the event loop if empty
```

Use the matching primitive for the scheduler:

| Between | Use |
|---------|-----|
| Async tasks on one loop | `asyncio.Queue`, `asyncio.Lock`, `asyncio.Semaphore` |
| Threads in one process | `queue.Queue`, `threading.Lock`, `threading.Event` |
| Processes | `multiprocessing.Queue`, `ProcessPoolExecutor`, external stores |
| Pods or machines | Redis, database locks, message queues, object storage |

When a thread must submit work to an event loop, do not touch asyncio objects directly:

```python
import asyncio

# From another OS thread:
loop.call_soon_threadsafe(callback, argument)
future = asyncio.run_coroutine_threadsafe(coroutine(), loop)
result = future.result(timeout=5)
```

`run_coroutine_threadsafe()` returns a `concurrent.futures.Future`; waiting on it from the event-loop thread would deadlock. It is a cross-thread bridge for synchronous thread code.

---

## 6. Safe Sharing Patterns

### Prefer immutable input and return values

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Job:
    user_id: int
    url: str
    timeout_s: float
```

Immutable messages are easy to pass to tasks, threads, and processes. If a worker needs to change something, let it return a new value.

### Use ownership instead of shared mutation

```python
import asyncio

async def worker(q: asyncio.Queue[str]):
    while True:
        url = await q.get()
        try:
            await fetch(url)
        finally:
            q.task_done()
```

The queue coordinates ownership. A task receives a work item, owns it, finishes it, then marks it done.

### Put shared state behind one API

```python
class ThreadSafeCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: bytes) -> None:
        with self._lock:
            self._data[key] = value
```

If state must be shared, centralize the synchronization. Do not make callers remember which lock protects which dict.

See [threads/02_synchronization_primitives.md](threads/02_synchronization_primitives.md) for lower-level lock, queue, condition variable, and signaling patterns.

### Externalize cross-process state

Processes do not share Python objects by default. If multiple workers need the same truth, use a system designed for coordination:

- PostgreSQL transactions and row locks.
- Redis atomic commands and Lua scripts.
- A message broker for ownership transfer.
- Object storage for large immutable blobs.

---

## 7. Separate Context Propagation from Resource Ownership

`ContextVar` is for logical execution context: request ID, tenant ID, trace ID, current user, or logging context. It is not a general shared-state container.

Good:

```python
request_id_var: ContextVar[str] = ContextVar("request_id", default="unknown")
```

Risky:

```python
current_request_data: ContextVar[dict[str, object]] = ContextVar("data")
```

The context variable binding is isolated, but the value can still be a mutable object. If two contexts point to the same dict, mutations affect the same dict.

Prefer small immutable values:

```python
request_id_var.set("req-123")
tenant_id_var.set("tenant-a")
```

Do not infer that a propagated object is safe to use concurrently. For example, two child tasks can inherit bindings to the same database session:

```python
session = session_var.get()

async with asyncio.TaskGroup() as group:
    group.create_task(load_profile(session))
    group.create_task(load_orders(session))
```

The bindings are context-local, but `session` is still one mutable object. If the session does not document concurrent use, give each operation a separate session or run the operations sequentially. The same rule applies to cursors, transactions, streams, and stateful clients.

See [async/03_contextvars.md](async/03_contextvars.md) for propagation rules.

---

## 8. The GIL Does Not Make Code Correct

On the default CPython build, the GIL prevents multiple threads from executing Python bytecode at the exact same time. It does not make compound operations atomic at the level your business logic cares about.

This is still a race:

```python
if key not in cache:
    cache[key] = compute()
```

Two threads can both observe the missing key and both compute. The fix is not "trust the GIL"; the fix is a lock, a single owner, or an atomic operation in an external system.

Free-threaded CPython builds make this even more important. Threads can execute Python bytecode in parallel, so accidental reliance on GIL-shaped behavior becomes a real correctness bug.

---

## 9. Process and Deployment Boundaries

Each process has its own Python interpreter and memory space. A module global is not a cluster-wide variable. It is one variable per process.

```text
gunicorn worker 1 -> cache = {}
gunicorn worker 2 -> cache = {}
gunicorn worker 3 -> cache = {}
```

If you run four Uvicorn/Gunicorn workers, you have four in-memory caches, four semaphores, and four sets of module globals. Use Redis, a database, or another shared system when the limit or state must apply globally.

---

## 10. Design Checklist

- Is this object mutable?
- Can more than one task/thread/process reach it?
- Can mutation happen across an `await` or while another thread is running?
- Is the primitive built for the scheduler I am using?
- Does the state need to be per request, per task, per thread, per process, or global?
- If this app runs with multiple workers or pods, does the design still work?
- Is the concurrency bounded?
- Is there a timeout?
- Is cancellation safe?
- Is the system still correct on a free-threaded Python build?

---

## References

- [`asyncio` synchronization primitives](https://docs.python.org/3/library/asyncio-sync.html)
- [`threading`](https://docs.python.org/3/library/threading.html)
- [`queue`](https://docs.python.org/3/library/queue.html)
- [`multiprocessing`](https://docs.python.org/3/library/multiprocessing.html)
- [`contextvars`](https://docs.python.org/3/library/contextvars.html)
- [Python free-threading HOWTO](https://docs.python.org/3/howto/free-threading-python.html)

---

**Next**: [Subinterpreters and Free-Threaded CPython](02_alternative_runtimes.md)
