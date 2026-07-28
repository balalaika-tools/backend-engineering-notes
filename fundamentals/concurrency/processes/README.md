# Processes

> Processes are the default answer for CPU-heavy pure Python work and for isolation from crashes, memory leaks, and shared-state bugs.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://docs.python.org/3/library/multiprocessing.html)

---

## Contents

| Guide | Topic |
|-------|-------|
| [01_process_pool_executor.md](01_process_pool_executor.md) | `ProcessPoolExecutor`, pickling, `__main__`, start methods, chunks, shutdown, and process-safe state. |
| [../01_state_and_safety.md](../01_state_and_safety.md) | What crosses process boundaries and what does not. |
| [../02_alternative_runtimes.md](../02_alternative_runtimes.md) | When subinterpreters or free-threaded CPython are credible alternatives. |

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

## Reading Order

1. **ProcessPoolExecutor** — learn the pickling boundary, start methods, sizing, cancellation limits, worker recycling, and shutdown.
2. **State and safety** — revisit process-local globals and external coordination before sharing state.
3. **Alternative runtimes** — compare subinterpreters or free-threaded Python only after the process model is clear.

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
- Treat future timeouts as limits on waiting, not proof that running CPU work stopped.

---

**Next**: [ProcessPoolExecutor](01_process_pool_executor.md)
