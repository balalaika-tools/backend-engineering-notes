# Processes

> Processes are the default answer for CPU-heavy pure Python work and for isolation from crashes, memory leaks, and shared-state bugs.

---

## Read these

| Guide | Topic |
|-------|-------|
| [01_process_pool_executor.md](01_process_pool_executor.md) | `ProcessPoolExecutor`, pickling, `__main__`, start methods, chunks, shutdown, and process-safe state. |
| [../01_state_and_safety.md](../01_state_and_safety.md) | What crosses process boundaries and what does not. |

---

## Mental model

A process has its own memory space and Python interpreter. That gives you real CPU parallelism on normal CPython, but data must cross the boundary by serialization, IPC, shared memory, or an external system.

Use processes for:

- Pure Python CPU-bound work.
- CPU-heavy preprocessing that does not rely on shared in-memory objects.
- Fault isolation.
- Work that should not be able to corrupt the main process.

Avoid process pools for tiny tasks where pickling and scheduling overhead cost more than the work itself.

---

## Commands

```bash
python -c "import os; print(getattr(os, 'process_cpu_count', os.cpu_count)())"
python -c "import multiprocessing as mp; print(mp.get_start_method())"
python -c "import multiprocessing as mp; print(mp.get_all_start_methods())"

# Unix process inspection
ps -o pid,ppid,stat,comm -p <PID>
pstree -p <PID>
```

`pstree` may need installation on macOS. `ps` is enough for most checks.

---

## Process design rules

- Keep process-pool functions importable at module top level.
- Always use the `if __name__ == "__main__":` guard for scripts that start processes.
- Pass small, picklable inputs and return small, picklable outputs.
- Batch tiny CPU tasks with `chunksize`.
- Do not depend on inherited globals or open sockets.
- Use an external system for state that must be global across workers or pods.
- Shut pools down cleanly during application shutdown.
