# APScheduler — Job Scheduling

APScheduler is an in-process scheduler. It decides **when** a job runs, not **where** or **how safely**. It is not a task queue.

---

## Installation

```bash
pip install apscheduler
```

For APScheduler 3.x (current stable). APScheduler 4.x is a major rewrite with different APIs (covered briefly at the end).

---

## Scheduler Types

| Scheduler | Use case | How it runs |
|-----------|----------|-------------|
| `AsyncIOScheduler` | Async apps (FastAPI, aiohttp) | On the running event loop |
| `BackgroundScheduler` | Sync apps (Flask, scripts) | In a background thread |
| `BlockingScheduler` | Standalone scripts | Blocks the main thread |

For FastAPI, always use **`AsyncIOScheduler`**.

---

## Trigger Types

### `interval` — Fixed Intervals

Run every N seconds/minutes/hours.

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

# Every 5 minutes
@scheduler.scheduled_job('interval', minutes=5)
async def refresh_cache():
    await cache.refresh_all()

# Every 30 seconds, starting at a specific time
@scheduler.scheduled_job('interval', seconds=30, start_date='2024-01-01 00:00:10')
async def health_ping():
    await ping_external_services()
```

Options: `weeks`, `days`, `hours`, `minutes`, `seconds`, `start_date`, `end_date`.

### `cron` — Cron-like Scheduling

Rich cron expressions.

```python
# Every day at 2:30 AM
@scheduler.scheduled_job('cron', hour=2, minute=30)
async def nightly_cleanup():
    await delete_expired_sessions()
    await vacuum_database()

# Every Monday at 9:00 AM
@scheduler.scheduled_job('cron', day_of_week='mon', hour=9, minute=0)
async def weekly_report():
    await generate_and_send_report()

# Every 15 minutes during business hours
@scheduler.scheduled_job('cron', hour='9-17', minute='*/15')
async def sync_inventory():
    await sync_from_erp()

# First day of every month at midnight
@scheduler.scheduled_job('cron', day=1, hour=0, minute=0)
async def monthly_billing():
    await run_billing_cycle()
```

Cron fields: `year`, `month`, `day`, `week`, `day_of_week`, `hour`, `minute`, `second`.

### `date` — One-time Execution

Run once at a specific time.

```python
from datetime import datetime, timedelta

# Run once, 1 hour from now
scheduler.add_job(
    send_reminder,
    trigger='date',
    run_date=datetime.now() + timedelta(hours=1),
    args=["user_123", "Complete your profile!"],
)
```

---

## Basic Usage

### Decorator Style

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job('interval', minutes=5, id='cache-refresh')
async def refresh_cache():
    await redis_client.refresh_hot_keys()

@scheduler.scheduled_job('cron', hour=3, minute=0, id='nightly-cleanup')
async def nightly_cleanup():
    deleted = await db.execute("DELETE FROM sessions WHERE expires_at < NOW()")
    logger.info(f"Cleaned up {deleted.rowcount} expired sessions")

scheduler.start()
```

### Programmatic Style

```python
scheduler = AsyncIOScheduler()

async def my_job(name: str):
    print(f"Running job: {name}")

# Add jobs programmatically
scheduler.add_job(my_job, 'interval', seconds=30, args=["heartbeat"], id="heartbeat")
scheduler.add_job(my_job, 'cron', hour=2, args=["nightly"], id="nightly")

scheduler.start()

# Modify a running job
scheduler.reschedule_job("heartbeat", trigger='interval', seconds=60)

# Pause / resume
scheduler.pause_job("heartbeat")
scheduler.resume_job("heartbeat")

# Remove
scheduler.remove_job("heartbeat")
```

---

## FastAPI Integration

### Using Lifespan (Recommended)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

async def cleanup_expired_tokens():
    async with get_db_session() as session:
        result = await session.execute(
            text("DELETE FROM tokens WHERE expires_at < NOW()")
        )
        logger.info(f"Cleaned up {result.rowcount} expired tokens")

async def refresh_materialized_views():
    async with get_db_session() as session:
        await session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY user_stats"))
        logger.info("Refreshed materialized views")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: configure and start scheduler
    scheduler.add_job(
        cleanup_expired_tokens,
        trigger='interval',
        minutes=10,
        id='token-cleanup',
    )
    scheduler.add_job(
        refresh_materialized_views,
        trigger='cron',
        hour=4,
        minute=0,
        id='refresh-views',
    )
    scheduler.start()
    logger.info("Scheduler started")

    yield

    # Shutdown: clean up
    scheduler.shutdown(wait=True)
    logger.info("Scheduler shut down")

app = FastAPI(lifespan=lifespan)
```

Do **not** use the deprecated `@app.on_event("startup")` pattern.

### Accessing the Scheduler from Endpoints

```python
@app.get("/scheduler/jobs")
async def list_jobs():
    """List all scheduled jobs (for debugging/admin)."""
    jobs = scheduler.get_jobs()
    return [
        {
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in jobs
    ]

@app.post("/scheduler/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    scheduler.pause_job(job_id)
    return {"status": "paused"}

@app.post("/scheduler/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    scheduler.resume_job(job_id)
    return {"status": "resumed"}

@app.post("/scheduler/jobs/{job_id}/run-now")
async def trigger_job(job_id: str):
    """Trigger immediate execution of a scheduled job."""
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.modify(next_run_time=datetime.now())
    return {"status": "triggered"}
```

---

## Sync vs Async Jobs

APScheduler's `AsyncIOScheduler` supports both sync and async job functions.

```python
# Async job — runs directly on the event loop
async def async_cleanup():
    await db.delete_expired()

# Sync job — runs in a thread (via run_in_executor)
def sync_heavy_computation():
    # CPU-bound work — runs in a thread so it doesn't block the event loop
    result = crunch_numbers()
    save_result(result)

scheduler.add_job(async_cleanup, 'interval', minutes=5)
scheduler.add_job(sync_heavy_computation, 'cron', hour=3)
```

> **Warning:** A sync job that takes a long time will occupy a thread from the default executor. An async job that does blocking work will **freeze the event loop**. Always use `async def` for I/O-bound work and `def` for CPU-bound work.

---

## Job Stores

By default, jobs are stored **in memory** — they are lost on restart.

### Memory Store (Default)

```python
scheduler = AsyncIOScheduler()
# Jobs live in memory. If the process restarts, all jobs are re-added from code.
# Fine for jobs defined in lifespan, since they're re-added on startup anyway.
```

### SQLAlchemy Store

```python
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

scheduler = AsyncIOScheduler(
    jobstores={
        'default': SQLAlchemyJobStore(url='postgresql://user:pass@localhost/mydb')
    }
)
```

### Redis Store

```python
from apscheduler.jobstores.redis import RedisJobStore

scheduler = AsyncIOScheduler(
    jobstores={
        'default': RedisJobStore(host='localhost', port=6379, db=2)
    }
)
```

### When to Use Persistent Stores

- **Memory (default):** Your jobs are defined in code and re-added on startup. This is the most common case.
- **SQLAlchemy / Redis:** You dynamically add jobs at runtime (e.g., user-scheduled reports) and need them to survive restarts. Also useful for `date` triggers (one-time jobs) that must not be lost.

---

## Misfire Grace Time

If a scheduled job is delayed (process was down, event loop was busy), APScheduler decides what to do based on `misfire_grace_time`:

```python
scheduler.add_job(
    nightly_cleanup,
    trigger='cron',
    hour=3,
    misfire_grace_time=3600,  # seconds — run if less than 1 hour late
)
```

- If the job is late by **less than** `misfire_grace_time` → it runs immediately
- If the job is late by **more than** `misfire_grace_time` → it is **skipped**
- Default is `1` second (very aggressive — will skip most misfired jobs)

> **Tip:** Set `misfire_grace_time` to a reasonable value for each job. For a nightly cleanup that runs at 3 AM, `3600` (1 hour) is sensible. For a real-time sync, `30` seconds may be appropriate.

---

## The Multi-Instance Problem

This is the **biggest footgun** with APScheduler. If you deploy multiple instances of your FastAPI app, each instance runs its own scheduler.

```
Pod A → scheduler → runs cleanup at 3:00 AM
Pod B → scheduler → runs cleanup at 3:00 AM  ← DUPLICATE!
Pod C → scheduler → runs cleanup at 3:00 AM  ← DUPLICATE!
```

There is **no leader election**, **no distributed locking**, **no shared state** in APScheduler 3.x. Each scheduler is completely independent.

### Solution 1: Single-Instance Deployment (Simplest)

Run the scheduler in **exactly one process** — a dedicated scheduler pod/container.

```yaml
# docker-compose.yml
services:
  api:
    command: uvicorn myapp.main:app --host 0.0.0.0
    deploy:
      replicas: 3  # no scheduler here
    environment:
      ENABLE_SCHEDULER: "false"

  scheduler:
    command: python -m myapp.scheduler
    deploy:
      replicas: 1  # exactly one instance
    environment:
      ENABLE_SCHEDULER: "true"
```

```python
# myapp/scheduler.py
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

async def cleanup():
    ...

async def main():
    scheduler.add_job(cleanup, 'cron', hour=3)
    scheduler.start()

    # Keep the process alive
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
```

Or conditionally in the same FastAPI process:

```python
import os

@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("ENABLE_SCHEDULER") == "true":
        scheduler.start()
    yield
    if os.environ.get("ENABLE_SCHEDULER") == "true":
        scheduler.shutdown(wait=False)
```

### Solution 2: Distributed Locking with Redis

If you must run the scheduler in every instance, use a distributed lock:

```python
import redis.asyncio as redis

redis_client = redis.Redis.from_url("redis://localhost:6379/0")

async def cleanup_with_lock():
    """Only one instance will execute the job body."""
    lock = redis_client.lock("scheduler:cleanup-lock", timeout=300)
    acquired = await lock.acquire(blocking=False)
    if not acquired:
        logger.info("Another instance is running cleanup, skipping")
        return
    try:
        await do_cleanup()
    finally:
        await lock.release()

scheduler.add_job(cleanup_with_lock, 'cron', hour=3)
```

This works but has edge cases: if the lock-holder crashes, the lock is held until timeout. Make the timeout longer than the job's maximum execution time.

### Solution 3: APScheduler as Trigger, Dramatiq as Executor

The cleanest approach for heavy workloads: use APScheduler **only** as a scheduler that enqueues Dramatiq tasks. Dramatiq handles deduplication, retries, and distributed execution.

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from myapp.tasks import nightly_cleanup_task  # Dramatiq actor

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job('cron', hour=3)
def trigger_nightly_cleanup():
    """Just enqueue the task — Dramatiq handles the rest."""
    nightly_cleanup_task.send()
```

This way:
- APScheduler answers **"when"** (3 AM)
- Dramatiq answers **"where"** (any available worker) and **"how safely"** (with retries, time limits)

---

## APScheduler 4.x (Preview)

APScheduler 4.x is a **complete rewrite** with a different API and architecture. Key changes:

- **Built-in data stores** (PostgreSQL, MySQL, MongoDB, Redis) for distributed scheduling
- **Built-in event broker** for coordinating multiple scheduler instances
- **Async-first** design
- **No more job stores confusion** — one unified store

```python
# APScheduler 4.x API (subject to change)
from apscheduler import AsyncScheduler
from apscheduler.datastores.sqlalchemy import SQLAlchemyDataStore
from apscheduler.eventbrokers.redis import RedisEventBroker

async with AsyncScheduler(
    data_store=SQLAlchemyDataStore("postgresql+asyncpg://..."),
    event_broker=RedisEventBroker("redis://localhost:6379/0"),
) as scheduler:
    await scheduler.add_schedule(
        cleanup_task, CronTrigger(hour=3), id="nightly-cleanup"
    )
    await scheduler.run_until_stopped()
```

APScheduler 4.x **solves the multi-instance problem** natively. Worth watching, but not production-ready for all use cases yet. Stick with 3.x + the workarounds above for now.

---

## Preventing Overlapping Runs

By default, if a job takes longer than its interval, the next invocation runs in parallel.

```python
scheduler.add_job(
    slow_sync,
    trigger='interval',
    minutes=5,
    max_instances=1,           # only one instance at a time
    replace_existing=True,
    id='slow-sync',
)
```

With `max_instances=1`, if the previous run is still going when the next trigger fires, the new invocation is **skipped** (a misfire).

---

## Dynamic Job Management

```python
# Add a job for a user-scheduled report
def schedule_user_report(user_id: str, cron_hour: int, cron_minute: int):
    scheduler.add_job(
        generate_user_report,
        trigger='cron',
        hour=cron_hour,
        minute=cron_minute,
        id=f"user-report-{user_id}",
        args=[user_id],
        replace_existing=True,     # update if already exists
        jobstore='default',
    )

# Remove when user cancels
def cancel_user_report(user_id: str):
    try:
        scheduler.remove_job(f"user-report-{user_id}")
    except JobLookupError:
        pass  # already removed
```

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Running scheduler in every pod | Use single-instance or distributed lock |
| Heavy work in scheduled job | Offload to Dramatiq worker |
| No error handling in jobs | Wrap in try/except — unhandled exceptions silently kill the job |
| Forgetting `scheduler.shutdown()` | Always shut down in lifespan `yield` cleanup |
| Using `BackgroundScheduler` with FastAPI | Use `AsyncIOScheduler` — it runs on the event loop |
| Default `misfire_grace_time=1` | Set a reasonable value or jobs get skipped silently |

---

## APScheduler vs Dramatiq for Periodic Tasks

| Scenario | Use |
|----------|-----|
| Simple periodic task, single instance | APScheduler |
| Periodic task, multiple instances | APScheduler + distributed lock, or APScheduler as trigger + Dramatiq |
| Heavy periodic task (minutes to hours) | APScheduler as trigger + Dramatiq |
| Task needs retries or result tracking | Dramatiq (APScheduler has neither) |
| Cron scheduling + lightweight work | APScheduler |
| No broker infrastructure available | APScheduler |
| User-configured schedules | APScheduler with persistent job store |

---

## Mental Model

> **APScheduler = cron for your Python process.** It answers "when" but not "where" or "how safely." If you need distributed-safe execution, use it only as a trigger that enqueues Dramatiq tasks.

```
APScheduler says:  "It's 3:00 AM, time to run cleanup"
                        │
                        ├── Simple case: run cleanup() right here in this process
                        │
                        └── Distributed case: cleanup_task.send()  → Dramatiq handles it
```

> **Rule of thumb:** If the job takes < 5 seconds and runs on a single instance, APScheduler is fine. For anything heavier or distributed, use APScheduler only as a cron trigger that enqueues work into Dramatiq.
