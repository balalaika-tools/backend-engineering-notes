# ThreadPoolExecutor vs ProcessPoolExecutor (Python)

This README explains **what Thread Pools and Process Pools are**, **how they behave in CPython**, and **when each one actually makes sense** — especially in the presence of the GIL.

---

## Core Concepts

### Process
- An **OS-level program**
- Has its **own memory space**
- Has its **own Python interpreter and GIL**
- Can run on a **separate CPU core**

➡ Processes enable **true CPU parallelism**

---

### Thread
- A **lightweight execution path inside a process**
- Threads **share memory**
- In CPython, all threads share **one GIL**

➡ Threads enable **concurrency**, not CPU parallelism (for Python bytecode)

---

## The GIL (Why this matters)

In CPython:
- **Only one thread executes Python bytecode at a time**
- Threads are time-sliced (they take turns every few ms)

Result:
- **Threads do NOT speed up CPU-bound Python code**
- They are still very useful for other reasons

---

## ThreadPoolExecutor

```python
from concurrent.futures import ThreadPoolExecutor
```

### What it gives you

* Multiple threads
* Shared memory
* Low overhead
* Fast task dispatch

### What it does NOT give you

* ❌ CPU speedup for pure Python computation
* ❌ True parallel execution of Python code

---

## ProcessPoolExecutor

```python
from concurrent.futures import ProcessPoolExecutor
```

### What it gives you

* Multiple processes
* One GIL per process
* Each process can use a **separate CPU core**
* **True CPU parallelism**

### Costs

* Higher startup cost
* Data must be **pickled / serialized**
* No shared memory by default

---

## When to Use ProcessPoolExecutor (CPU-bound)

Use **ProcessPoolExecutor** when:

* Work is **CPU-bound**
* Code is **pure Python**
* Tasks are relatively heavy (ms–seconds)
* You want real speedup across cores

### Examples

* Feature engineering loops
* Text parsing / tokenization
* Image preprocessing in Python
* Data transformations
* Custom ML preprocessing

➡ Rule of thumb:

```
number of processes ≈ number of CPU cores
```

---

## When ThreadPoolExecutor Actually Makes Sense

Thread pools **do have valid use cases**, even for "CPU-looking" tasks.

### The 3 Legitimate Exceptions

---

### 1. I/O-bound Work (Most Common)

Threads shine when tasks spend time **waiting**, not computing.

Examples:

* HTTP requests
* Database queries
* File reads/writes
* Network I/O

Why it works:

* While one thread waits for I/O, another runs
* GIL is released during blocking I/O

➡ ThreadPoolExecutor is excellent here.

---

### 2. Work Done in C Extensions That Release the GIL

Many libraries release the GIL internally:

* NumPy
* PyTorch (many ops)
* OpenCV
* zlib / hashlib
* PIL (some operations)

In these cases:

* Threads can run **C code in parallel**
* Multiple CPU cores are actually used

➡ ThreadPoolExecutor *can* scale here.

---

### 3. Responsiveness (Latency / UX), Not Throughput

Threads are useful when:

* You **do not care about total speed**
* You **do care about not blocking the main thread**

Typical scenarios:

* GUI applications
* Web servers
* Event-driven systems

Example:

```python
def on_click():
    executor.submit(heavy_compute)  # background
    update_ui()                     # immediate
```

What happens:

* Heavy computation still takes the same total time
* But the main thread remains responsive
* User experience improves dramatically

➡ You gain **lower latency**, not higher throughput.

---

## What About Async?

Async (`asyncio`) is:

* Excellent for **I/O-bound concurrency**
* Single-threaded
* Very low overhead
* Scales to thousands of tasks

Async is **not** for CPU-bound work.

Rule:

* I/O-bound + async-compatible libs → `asyncio`
* I/O-bound + blocking libs → ThreadPoolExecutor
* CPU-bound → ProcessPoolExecutor

---

## Summary Table

| Workload Type                         | Best Tool                     |
| ------------------------------------- | ----------------------------- |
| Pure Python CPU-bound                 | ProcessPoolExecutor           |
| Heavy preprocessing                   | ProcessPoolExecutor           |
| I/O-bound (network, disk)             | ThreadPoolExecutor or asyncio |
| C-extension heavy (NumPy, Torch)      | ThreadPoolExecutor            |
| GUI / server responsiveness           | ThreadPoolExecutor            |
| Massive concurrency (I/O)             | asyncio                       |
| Shared memory, no GIL (`python3.14t`) | ThreadPoolExecutor            |

---

## One Sentence to Remember

> **Processes are for speed.
> Threads are for waiting, sharing, or staying responsive.**

---

## Free-Threaded Python (No-GIL)

The GIL is being made **optional**, not removed. There are now two CPython builds:

* **Default build** (`python3.14`) — still has the GIL, semantics unchanged
* **Free-threaded build** (`python3.14t`) — threads execute Python bytecode in **parallel** across cores

### Status timeline

| Release | Status | PEP |
|---------|--------|-----|
| Python 3.13 (Oct 2024) | Experimental — opt-in via `python3.13t` | [PEP 703](https://peps.python.org/pep-0703/) |
| Python 3.14 (Oct 2025) | **Officially supported** — still opt-in | [PEP 779](https://peps.python.org/pep-0779/) |
| Future | Default build (no firm timeline) | — |

### What changed in 3.14

* PEP 779 moved free-threaded Python from "experimental" to **Phase II: officially supported**
* The free-threaded HOWTO reports average single-thread overhead of about **1% on macOS aarch64 to 8% on x86-64 Linux** on `pyperformance`
* Built-in containers use internal locks in the current implementation, but Python still does not guarantee specific behavior for unsynchronized concurrent mutation
* Some extension modules can force the GIL back on at import time if they are not marked as free-threading-safe
* FastAPI 0.136.0+ has public free-threaded-Python support, but real throughput gains remain workload- and dependency-dependent

### What to know before using it

* You need the `python3.14t` binary (or build CPython with `--disable-gil`) — the default `python3.14` is unchanged
* Check whether the GIL is actually disabled with `sys._is_gil_enabled()` or `sysconfig.get_config_var("Py_GIL_DISABLED")`
* C extensions must explicitly support the free-threaded build; otherwise they may re-enable the GIL
* Shared mutable state is now genuinely concurrent — use `threading.Lock` or another synchronization primitive instead of relying on incidental atomicity

### Practical rule today

* **I/O-bound, most apps** → standard `python3.14`, `asyncio` or threads as usual
* **CPU-bound in pure Python** → multiprocessing remains the safe default; free-threaded `python3.14t` is a serious option if every dependency supports it
* **FastAPI with mixed workloads** → the free-threaded build becomes compelling when CPU-bound endpoints are the bottleneck and you'd rather not run a separate worker tier

---

## Final Practical Rule

If your goal is:

* **"Finish faster"** → ProcessPoolExecutor
* **"Don't block the main thread"** → ThreadPoolExecutor
* **"Handle lots of I/O cleanly"** → asyncio

---

## References

* [PEP 703 — Making the GIL Optional](https://peps.python.org/pep-0703/)
* [PEP 779 — Supported status for free-threaded Python](https://peps.python.org/pep-0779/)
* [Python free-threading HOWTO](https://docs.python.org/3/howto/free-threading-python.html)
* [`concurrent.futures`](https://docs.python.org/3/library/concurrent.futures.html)
