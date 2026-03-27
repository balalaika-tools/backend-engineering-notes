# Task Execution Strategies — Overview

Three tools, three jobs. Pick the right one.

---

## 1. Dramatiq (Distributed Background Workers)

**What it is:** A simple, reliable distributed task processing library for Python. Think of it as "Celery but without the pain."

### Architecture

```
Client (FastAPI)
    │
    ▼
Broker (Redis or RabbitMQ)
    │
    ▼
Dramatiq Worker(s)   ← separate process(es), possibly on different machines
```

The FastAPI process **enqueues a message** (serialized function call). One or more Dramatiq worker processes **consume messages** and execute the corresponding function. The broker sits in the middle as a durable queue.

### Key Characteristics

- **Separate processes** — workers run independently from your API server
- **Survives restarts** — messages live in the broker; if a worker dies, another picks up the work
- **Retries with backoff** — built-in exponential backoff, configurable per-task
- **Time limits** — kill tasks that run too long
- **Rate limiting** — control concurrency against external APIs
- **Horizontal scaling** — add more worker processes or machines as load grows

### Pros

- Simple, Pythonic API (`@dramatiq.actor` + `.send()`)
- Sensible defaults out of the box (retries, timeouts, logging)
- Good error handling and dead-letter queues
- Lightweight — no bloated dependency tree
- First-class support for pipelines and groups

### Cons

- Requires broker infrastructure (Redis or RabbitMQ)
- Workers are synchronous by default (use threads, not async)
- Smaller ecosystem than Celery (fewer third-party integrations)

### Use When

- Tasks are **critical** and must not be lost (payment processing, document generation)
- Execution time ranges from **seconds to hours**
- Tasks are **CPU-heavy** or call **slow external APIs**
- You need **retries and delivery guarantees**
- You run **multiple API instances** and tasks must execute exactly once

---

## 2. FastAPI BackgroundTasks

**What it is:** Built-in fire-and-forget task execution that runs **inside your FastAPI process**.

### How It Works

```python
from fastapi import BackgroundTasks

@app.post("/send-email")
async def send_email(background_tasks: BackgroundTasks):
    background_tasks.add_task(actually_send_email, "user@example.com")
    return {"status": "queued"}
```

- If the task function is `async def` → runs on the **same event loop** (cooperative multitasking)
- If the task function is a plain `def` → runs in a **threadpool** (`anyio` worker thread)

### Pros

- Zero configuration — nothing to install, no broker, no extra process
- Simple API — just `add_task(fn, *args)`
- Access to the same in-memory state as your request handlers
- Perfect for lightweight post-response work

### Cons

- **Dies on restart** — if the process crashes, queued tasks are lost forever
- **No retries** — if the task fails, it fails silently
- **Shares resources** — CPU-heavy tasks block your event loop or exhaust your threadpool
- **No visibility** — no dashboard, no task status, no result storage
- **No horizontal scaling** — task runs on whichever instance received the request

### Use When

- Sending **notification emails** or **webhooks** after a response
- **Logging** or **analytics** that is nice-to-have but not critical
- Tasks complete in **under a few seconds**
- You are running a **single instance** or don't care about task loss

---

## 3. AsyncIOScheduler (APScheduler)

**What it is:** An in-process job scheduler. It decides **when** to run something, not **where** or **how safely**.

### How It Works

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job('cron', hour=3, minute=0)
async def nightly_cleanup():
    await delete_expired_sessions()

scheduler.start()  # runs inside the same event loop as FastAPI
```

### Pros

- **Cron-like scheduling** — intervals, cron expressions, one-time dates
- **Async-native** — `AsyncIOScheduler` runs coroutines directly on the event loop
- **No external dependencies** — no broker, no extra processes
- Persistent job stores available (SQLAlchemy, Redis)

### Cons

- **Shares the event loop** — a slow scheduled job blocks your API
- **No retries** — if a job fails, it just logs an error
- **No horizontal scaling** — the scheduler runs inside one process
- **Duplicate execution in multi-instance** — if you run 3 pods, the job runs 3 times
- **Not a task queue** — no message passing, no result backend, no priorities

### Use When

- **Periodic maintenance** — cleanup expired tokens, rotate logs
- **Cache refresh** — warm caches on a schedule
- **Health checks** — ping external services periodically
- You run a **single instance** or handle deduplication yourself

---

## Comparison Table

| Feature | BackgroundTasks | APScheduler | Dramatiq | Airflow |
|---|---|---|---|---|
| **Execution model** | In-process | In-process | Separate worker processes | Separate platform (scheduler + executor) |
| **Scheduling** | Immediate (fire-and-forget) | Cron / interval / date | Immediate + optional delay | Cron + catchup + data-aware |
| **Task dependencies** | None | None | Basic pipelines | Full DAGs (fan-out, fan-in, branching) |
| **Survives restart** | No | No (memory store) | Yes (messages in broker) | Yes (metadata DB) |
| **Retries** | No | No | Yes (configurable backoff) | Yes (per-task, with SLA) |
| **Time limits** | No | No | Yes | Yes (execution_timeout + dagrun_timeout) |
| **Rate limiting** | No | No | Yes | Via pool slots |
| **Result tracking** | No | No | Yes (Redis) | Yes (XCom + metadata DB) |
| **Multi-instance safe** | N/A | No (duplicates) | Yes | Yes (single scheduler) |
| **Horizontal scaling** | No | No | Yes (add workers) | Yes (Celery/K8s executor) |
| **Infrastructure** | None | None | Redis or RabbitMQ | Scheduler + web server + PostgreSQL |
| **Monitoring** | None | None | Dashboard, Prometheus | Rich web UI, Gantt charts, logs |
| **Best for** | Quick post-response work | Periodic/scheduled tasks | Reliable distributed work | Multi-step pipelines, ETL, ML workflows |

---

## Why Not Celery?

Celery is the most well-known Python task queue, but it carries significant baggage:

- **Complex configuration** — dozens of settings with confusing names (`task_acks_late`, `worker_prefetch_multiplier`, `broker_transport_options`)
- **Memory leaks** — long-running workers are notorious for growing memory usage over time
- **Poor async story** — bolted-on async support that doesn't integrate cleanly
- **Heavy dependency tree** — pulls in many transitive dependencies
- **Surprising defaults** — tasks acknowledge before execution by default (so failures lose messages)

Dramatiq covers **95% of Celery's use cases** with a simpler API and better defaults. Consider Celery only if:
- You need **canvas** (complex task workflows: chains, chords, groups with callbacks)
- You have an **existing Celery infrastructure** and the migration cost is too high
- You need a specific Celery-only integration (e.g., `django-celery-beat`)

---

## Mental Model

> **BackgroundTasks** = "run this later, inside FastAPI"
>
> **APScheduler** = "run this at a specific time"
>
> **Dramatiq** = "run this safely, somewhere else"
>
> **Airflow** = "run these steps in the right order, on a schedule, with a dashboard"

If you are unsure, start with **BackgroundTasks**. When you outgrow it (need retries, survive restarts, or scale workers), move to **Dramatiq**. Use **APScheduler** only for the scheduling question — and even then, consider having it enqueue Dramatiq tasks rather than doing the work itself. Reach for **Airflow** when you have multi-step workflows with dependencies between tasks (ETL pipelines, ML training, data quality checks).

---

## Decision Flowchart

```
Does the task have dependencies on other tasks (DAG)?
├── Yes → Airflow
└── No
    ├── Is the task critical (must not be lost)?
    │   ├── Yes → Dramatiq
    │   └── No
    │       ├── Does it need to run on a schedule?
    │       │   ├── Yes → APScheduler (or APScheduler → Dramatiq for heavy work)
    │       │   └── No
    │       │       ├── Will it take < 5 seconds?
    │       │       │   ├── Yes → BackgroundTasks
    │       │       │   └── No → Dramatiq
    │       │       └──
    │       └──
    └──
```

---

## Final Rule of Thumb

> **Web servers handle requests.**
> **Workers handle work.**
> **Schedulers handle timing.**
> **Orchestrators handle dependencies.**

Mixing these responsibilities leads to scaling and reliability issues.
