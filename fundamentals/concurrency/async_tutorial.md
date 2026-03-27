# Understanding Asyncio, Event Loops, and Concurrency in Python

This document explains **how asyncio works**, **what concurrency actually means**, and **how to correctly combine async code with threads and processes** — without falling into common performance traps.

---

## 🔹 What is `async` and the Event Loop?

In Python, `async` / `await` is syntax for **coroutines**: functions that can *pause* execution so other work can run.

The **event loop** is:
- single-threaded
- a scheduler
- responsible for resuming coroutines when they are ready

### Mental model
Think of the event loop as **one very fast coordinator**, not a worker.

- If a coroutine is waiting for I/O → the loop runs something else.
- If a coroutine does CPU-heavy work → the loop is blocked.

Async does **not** give you CPU parallelism.

---

## 🔹 Coroutines vs Tasks

### Coroutine
- A coroutine is a **definition of async work**
- Calling an `async def` function returns a coroutine object
- It does **nothing** until executed or scheduled

```python
coro = fetch_data()  # not running
```

---

### Task

* A **Task** is a coroutine scheduled on the event loop
* Created with `asyncio.create_task`
* Starts running immediately

```python
task = asyncio.create_task(fetch_data())  # running
```

➡ Coroutine = recipe
➡ Task = cooking has started

---

## 🔹 Awaiting a Coroutine vs Creating a Task

There are **two different ways** to execute a coroutine.

---

### 1️⃣ `await some_coroutine()`

```python
result = await fetch_data()
```

What this means:

* The coroutine **starts immediately**
* The **current coroutine pauses** until it finishes
* Execution is **sequential from the caller's perspective**

Mental model:

> "Run this now, and don't continue *me* until it's done."

---

### 2️⃣ `asyncio.create_task(some_coroutine())`

```python
task = asyncio.create_task(fetch_data())
```

What this means:

* The coroutine runs **concurrently**
* The caller continues immediately
* You must `await` the task later (or use a `TaskGroup`)

Mental model:

> "Start this in the background and manage it later."

---

### 🔑 Key Difference

```python
await fetch_data()
```

* Blocks **this coroutine**
* No concurrency unless other tasks already exist

```python
asyncio.create_task(fetch_data())
```

* Enables concurrency
* Does **not** block the caller

---

## 🔹 Sequential Await (One Coroutine)

```python
import asyncio, time

async def main():
    for i in range(5):
        print(f"Start {i} at {time.strftime('%X')}")
        await asyncio.sleep(5)
        print(f"End {i} at {time.strftime('%X')}")

asyncio.run(main())
```

⏱ Total runtime ≈ **25 seconds**

Why?

* Only **one coroutine exists**
* When it awaits, nothing else can run
* The event loop idles

---

## 🔹 True Async Concurrency with Multiple Tasks

```python
async def sleeper(i):
    print(f"Start {i}")
    await asyncio.sleep(5)
    print(f"End {i}")
```

### Using `TaskGroup` (Python 3.11+)

```python
import asyncio

async def main():
    async with asyncio.TaskGroup() as tg:
        for i in range(5):
            tg.create_task(sleeper(i))

asyncio.run(main())
```

⏱ Total runtime ≈ **5 seconds**

Why?

* Multiple tasks exist
* When one task awaits, others can run
* Single thread, overlapping I/O

---

## 🔹 Why `TaskGroup` Instead of `gather`

`TaskGroup` provides **structured concurrency**:

* Tasks belong to a clear scope
* If one task fails → others are cancelled
* No orphaned background tasks
* Clean error propagation

➡ Prefer `TaskGroup` over `gather` in modern code.

---

## ⚠️ Forgetting to await Tasks 

When you do:

```python
task = asyncio.create_task(fetch_data())
```

you have **started work** and got back a **Task handle**.

The mistake is not "you didn't use `await` right away".
The mistake is:

> **You started a task and then you never await it, never store it, and never handle its errors.**

That's what "forgetting to await" really means.

---

### ✅ Starting early and awaiting later (good pattern)

This is a common and valid optimization: start I/O early, do other work, then await when you need the result.

```python
async def main():
    task = asyncio.create_task(fetch_data())  # start I/O early

    # do other useful work while fetch_data is waiting on I/O
    await asyncio.sleep(0.2)  # placeholder for real work

    result = await task  # collect result + propagate exceptions
    print("Result:", result)
```

Why this is good:

* You overlap I/O with other work
* You **still** get the result
* Exceptions are handled normally (they propagate at `await task`)

---

### ❌ Fire-and-forget without supervision (dangerous)

```python
async def main():
    asyncio.create_task(fetch_data())
    print("Done")
```

What can go wrong:

1. **You lost the handle**

* You can't await it later
* You can't cancel it
* You can't see its result

2. **Exceptions may be lost**
   If `fetch_data()` raises, you may only see a warning like:

```
Task exception was never retrieved
```

3. **It may not finish in short-lived programs**
   If your program ends shortly after (`asyncio.run(main())` exits), remaining tasks are typically cancelled during shutdown.

➡ In long-running servers (FastAPI), tasks usually *won't* be cancelled just because the endpoint returns — but you still have the "lost errors / no control" problem.

---

## 🔹 Async in FastAPI (Real Example)
 
 Each request handler is a coroutine.
 
```python
@app.get("/sleep/{n}")
async def sleep_endpoint(n: int):
    await asyncio.sleep(5)
    return {"request": n}
```
 
 Multiple requests:
 
 * overlap in time
 * do not block each other
 * as long as they await I/O
 
 ---

## 🔹 `async with`: Asynchronous Context Managers

Just like `with` is used for resource management in synchronous code,
`async with` is used for **asynchronous setup and cleanup**.

It is required when:

* acquiring resources asynchronously
* cleanup itself requires `await`

---

### Example: Asynchronous File Reading

Using `aiofiles` (common async I/O library):

```python
import aiofiles

async def read_file(path):
    async with aiofiles.open(path, mode="r") as f:
        contents = await f.read()
    return contents
```

What happens:

* File is opened **without blocking** the event loop
* `await f.read()` yields control while waiting for disk I/O
* File is closed asynchronously when exiting the block

Mental model:

> "Acquire resource asynchronously → use it → release asynchronously."

---

### Another Example: `TaskGroup` Uses `async with`

```python
async with asyncio.TaskGroup() as tg:
    tg.create_task(fetch_data())
    tg.create_task(fetch_data())
# all tasks finished or cancelled here
```

`async with` ensures:

* tasks complete
* cancellations propagate
* no leaks

---

## 🔹 CPU-Bound Code Breaks Async

```python
async def crunch():
    total = 0
    for i in range(10**8):
        total += i
    return total
```

This:

* blocks the event loop
* freezes all other tasks
* defeats async entirely

---

## 🔹 Correct Way to Handle CPU-Bound Work

### Core rule

> Asyncio orchestrates.
> **Threads or processes do the work.**

---

## 🔹 ThreadPoolExecutor (No CPU Speedup)
```python
pool = ThreadPoolExecutor(max_workers=10)

def crunch_blocking(n):
    total = 0
    for i in range(10**8):
        total += i
    return total
 
async def crunch_async(n):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, crunch_blocking, n)

# later do --> pool.shutdown(wait=True)
 ```
Threads are useful for:

* blocking I/O
* C extensions that release the GIL
* responsiveness (not throughput)

They **do not** speed up pure Python CPU work.

---

## 🔹 ProcessPoolExecutor (True CPU Parallelism)

### ❌ Anti-pattern (DO NOT DO THIS)

```python
async def process_async(x):
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor() as pool:
        return await loop.run_in_executor(pool, cpu_heavy_work, x)
```

Why this is bad:

* Spawns processes on every call
* Massive overhead
* Terrible scaling

---

### ✅ Correct Pattern: Long-Lived Process Pool

```python
from concurrent.futures import ProcessPoolExecutor
import asyncio

pp = ProcessPoolExecutor()

def cpu_heavy_work(x):
    return sum(range(10**7)) + x

async def process_async(x):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pp, cpu_heavy_work, x)
```

### Clean shutdown (important!)

```python
pp.shutdown()
```

---

## 🔹 Using TaskGroup with ProcessPool

```python
async def main():
    async with asyncio.TaskGroup() as tg:
        tg.create_task(process_async(1))
        tg.create_task(process_async(2))
        tg.create_task(process_async(3))
```

This gives you:

* real CPU parallelism (via processes)
* async orchestration
* structured cancellation & errors

---

## 🔹 `asyncio.to_thread()` (Python 3.9+)

Shortcut for thread offloading:

```python
result = await asyncio.to_thread(blocking_io_function)
```

Good for:

* quick fixes
* blocking I/O
* **not** heavy CPU work

---

## 🔹 When Async Is Not Enough

For larger systems, consider:

* **Celery / RQ** → durable background jobs
* **Dedicated workers** → isolation & retries
* **vLLM / TGI** → high-throughput GPU inference

Async is not a replacement for job queues.

---

## ✅ Summary Table

| Tool                  | Best For                     | Notes                  |
| --------------------- | ---------------------------- | ---------------------- |
| `asyncio`             | I/O-bound concurrency        | Single-threaded        |
| `TaskGroup`           | Managing async tasks         | Structured concurrency |
| `ThreadPoolExecutor`  | Blocking I/O, responsiveness | No CPU speedup         |
| `ProcessPoolExecutor` | CPU-bound Python             | True parallelism       |
| `asyncio.to_thread`   | Quick blocking offload       | Thread-based           |
| Job queues            | Long-running jobs            | Durable, scalable      |

---

## Final Mental Model

* **Async** = coordination
* **Threads** = waiting & sharing
* **Processes** = speed

> If something is slow because it *waits* → async or threads
> If something is slow because it *thinks* → processes

---
