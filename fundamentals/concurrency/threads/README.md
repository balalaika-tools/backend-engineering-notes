# Threads

> Threads are useful when you need shared memory, blocking I/O integration, responsiveness, or C-extension work that releases the GIL.

---

## Read these

| Guide | Topic |
|-------|-------|
| [01_thread_pool_executor.md](01_thread_pool_executor.md) | `ThreadPoolExecutor`, futures, `map`, `submit`, timeouts, shutdown, and deadlocks. |
| [02_synchronization_primitives.md](02_synchronization_primitives.md) | Locks, try-locks, semaphores, conditions, queues, signaling, reader-writer patterns, livelock, and CAS caveats. |
| [../01_state_and_safety.md](../01_state_and_safety.md) | Which objects can be shared, which need locks, and what "thread-safe" really means. |

---

## Mental model

Threads run inside one process and share that process memory. That makes communication cheap and bugs easy.

Use threads for:

- Blocking I/O libraries when async clients are unavailable.
- Keeping a main thread or event loop responsive.
- C-extension-heavy work that releases the GIL.
- Small amounts of shared in-memory coordination with locks.

Do not expect standard CPython threads to speed up pure Python CPU loops on the default GIL build. Use processes for that unless you are deliberately targeting a free-threaded build and have tested your dependency graph.

---

## Safe primitives

| Primitive | Use for |
|-----------|---------|
| `threading.Lock` | Protect one shared mutable invariant. |
| `threading.RLock` | Re-entrant locking when the same thread must acquire again. |
| `threading.Event` | Signal one-way readiness or shutdown. |
| `threading.Condition` | Wait for a predicate while holding a lock. |
| `threading.Semaphore` | Limit concurrent access to a resource. |
| `queue.Queue` | Thread-safe producer/consumer handoff. |
| `threading.local` | Per-thread state, not async request state. |

---

## Commands

```bash
python -c "import threading; print(threading.active_count())"
python -c "import os; print(os.cpu_count())"

# Free-threading checks on supported builds
python -c "import sysconfig; print(sysconfig.get_config_var('Py_GIL_DISABLED'))"
python -c "import sys; print(getattr(sys, '_is_gil_enabled', lambda: 'unknown')())"

# Inspect a running process on Unix-like systems
ps -M <PID>
py-spy top --pid <PID>
```

`ps -M` is available on macOS. On Linux, use `ps -L -p <PID>` or `top -H -p <PID>`.

---

## Thread design rules

- Protect shared mutable state with one clear lock.
- Keep lock scopes short.
- Do not call slow external services while holding a lock.
- Do not hold a thread lock across `await`.
- Prefer `queue.Queue` for handoff instead of shared lists.
- Use timeouts on blocking waits where shutdown matters.
- Treat the GIL as an implementation detail, not a correctness primitive.

For the lower-level primitive details, see [02_synchronization_primitives.md](02_synchronization_primitives.md).
