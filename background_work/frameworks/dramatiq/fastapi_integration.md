# Dramatiq + FastAPI Integration

> **Who this is for**: FastAPI engineers who have already chosen Dramatiq and need durable submission, status, testing, and deployment boundaries.

Before reading this, understand **[Dramatiq’s runtime](overview.md)** and the **[database/broker outbox boundary](../../04_queue_and_worker_architectures.md#3-a-broker-needs-an-outbox-at-the-database-boundary)**.

FastAPI and Dramatiq share application code, but run in separate processes. FastAPI validates a command and records intent; Dramatiq delivers IDs to a worker; the database remains authoritative for user-visible status.

> **Key insight**: Returning a Dramatiq message ID is not durable job tracking; create a domain job record and close the database/broker publication gap with an outbox.

---

## Project layout: broker before actors, always

When API and worker processes import the same task package in different orders, a hidden default broker can send messages to the wrong place. Use one explicit initialization module and import it before actor declarations.

```
myapp/
├── __init__.py
├── main.py              # FastAPI app + lifespan
├── broker.py            # Dramatiq broker setup (imported before actors)
├── tasks.py             # Actor definitions
├── config.py            # Settings (pydantic-settings)
├── middleware.py         # Custom Dramatiq middleware
├── docker-compose.yml   # Redis + API + Worker
├── Dockerfile
└── tests/
    ├── __init__.py
    ├── conftest.py      # Stub broker fixtures
    └── test_tasks.py    # Tests with stub broker
```

The critical rule: **`broker.py` must be imported before any module that defines actors.** Dramatiq needs a configured broker before `@dramatiq.actor` decorators run.

---

## Config: one settings object, both processes

```python
# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    redis_url: str = "redis://localhost:6379/0"
    redis_result_url: str = "redis://localhost:6379/1"

settings = Settings()
```

---

## One broker module, imported before every actor

```python
# broker.py
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.results import Results
from dramatiq.results.backends import RedisBackend

from .config import settings

# Create and configure the broker
broker = RedisBroker(url=settings.redis_url)

# Add result backend (optional — only if you need to retrieve task results)
result_backend = RedisBackend(url=settings.redis_result_url)
broker.add_middleware(Results(backend=result_backend))

# Set as the global broker — must happen before any @dramatiq.actor decorators
dramatiq.set_broker(broker)
```

**Why a separate DB for results?** It keeps result keys from colliding with broker queues — a `FLUSHDB` on the result database does not take the queues with it.

⚠️ **Numbered databases are namespacing, not isolation, and they are not portable.** Redis Cluster supports **only database 0**, so `redis://.../1` fails the moment you move to a clustered or managed-clustered Redis (`ERR SELECT is not allowed in cluster mode`), and every deployment that assumed two DBs has to be re-plumbed. They are also not a security boundary: a single `AUTH` grants access to every database on the instance, so a compromised API process can read and delete worker results.

Portable equivalents, in increasing order of strength:

- **Key prefixes on one database.** This is already the default — `RedisBroker` prefixes with `namespace="dramatiq"`, `RedisBackend` with `namespace="dramatiq-results"` — so queues and results cannot collide even on database 0. Override both when several applications share an instance: `RedisBroker(url=..., namespace="myapp")`, `RedisBackend(url=..., namespace="myapp-results")`. Cluster-safe, and it survives a migration unchanged.
- **Separate Redis instances** when the two workloads have different durability or memory-eviction needs (results are disposable; queues are not — an `allkeys-lru` policy that evicts a queue entry loses a job).
- **Separate instances plus per-service ACL users** when isolation is a security requirement rather than a tidiness one. See [the broker security boundary](overview.md#the-broker-is-an-rpc-endpoint--secure-it-like-one).

---

## One actor module, imported through the broker

The `process_document` below is a **baseline** that shows the result-backend shape. It is superseded later in this note by the `(job_id, doc_id)` version in [Fix 2](#fix-2--the-actor-claims-an-attempt-and-only-that-attempt-may-finish-it) — that is the signature every later block uses and the one to ship. Do not assemble the project from both.

```python
# tasks.py
import dramatiq
import logging

# This import ensures the broker is configured before actors are defined
from .broker import broker  # noqa: F401 — side effect import

logger = logging.getLogger(__name__)

@dramatiq.actor(store_results=True, max_retries=3, time_limit=300_000)
def process_document(doc_id: str) -> dict:
    """Process a document. Runs in a Dramatiq worker, not in FastAPI."""
    logger.info(f"Processing document {doc_id}")

    document = fetch_document(doc_id)
    result = run_pipeline(document)
    save_result(doc_id, result)

    logger.info(f"Finished processing document {doc_id}")
    return {"doc_id": doc_id, "status": "processed", "pages": result.page_count}


@dramatiq.actor(max_retries=5, min_backoff=2_000)
def send_notification(user_id: str, message: str):
    """Send a push notification to a user."""
    logger.info(f"Sending notification to {user_id}")
    notification_service.send(user_id, message)


@dramatiq.actor(queue_name="low-priority", max_retries=1)
def generate_report(report_id: str, params: dict):
    """Generate a large report — low priority, long running."""
    logger.info(f"Generating report {report_id}")
    build_report(report_id, params)
```

⚠️ **If you forget the broker import, the tasks do not go nowhere — they go somewhere worse.** `get_broker()` builds a default broker on first use: it tries RabbitMQ, and when the RabbitMQ dependencies are absent — exactly the case for the `dramatiq[redis]` install this note uses — it falls back to a **Redis broker at `localhost:6379/0`**. On a developer laptop that is often the same Redis as `settings.redis_url`, so everything appears to work; in every other environment the API publishes to an unconfigured default while the worker consumes the configured one.

The tell: messages present at the default coordinates (`redis-cli -u redis://localhost:6379/0 hlen dramatiq:default.msgs` returns a non-zero count) and absent from `settings.redis_url`. Source: [`dramatiq/broker.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/broker.py) (`get_broker` docstring), checked 2026-08-03.

---

## Endpoints record intent and return an ID

```python
# main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import dramatiq.results

# Import tasks — this triggers broker.py, which configures the broker
from .tasks import process_document, send_notification, generate_report


app = FastAPI(title="Document Processing API")


# --- Enqueue a task ---

@app.post("/documents/{doc_id}/process", status_code=202)
async def start_processing(doc_id: str):
    """Enqueue a document for processing. Returns immediately."""
    message = process_document.send(doc_id)
    # One response contract for this endpoint everywhere in this note:
    # {"job_id": <str>, "state": "PENDING"}. The durable version below keeps
    # the same two keys and only changes where job_id comes from.
    return {"job_id": message.message_id, "state": "PENDING"}


# --- Check task status ---
#
# WARNING: Dramatiq's result backend keys results off the *original* Message
# object (specifically its auto-generated `message_id`), not off a job id you
# pass in. You CANNOT reconstruct a working lookup from a bare id string: a
# freshly built Message gets a brand-new random `message_id`, so its result key
# never matches what the worker stored. To read a result you must hold the
# Message object that `.send()` returned — which does not survive across API
# instances or restarts. So `get_result` is only usable in narrow cases (e.g.
# the same worker that you also block on). For durable, cross-instance status
# lookup, track job state in YOUR OWN database — see "Track status in your own
# database" below, which is the pattern you should actually ship.
#
# The snippet below shows the result API given a Message you still hold:

# message = process_document.send(doc_id)   # keep this reference
# try:
#     result = message.get_result(block=False)   # ResultMissing if not ready
# except dramatiq.results.ResultMissing:
#     result = None


# --- Fire-and-forget tasks ---

class NotifyRequest(BaseModel):
    message: str

@app.post("/users/{user_id}/notify")
async def notify_user(user_id: str, req: NotifyRequest):
    """Send a notification — no result needed."""
    send_notification.send(user_id, req.message)
    return {"status": "sent"}


# --- Delayed task ---

@app.post("/reports")
async def create_report(params: dict):
    """Enqueue a report to be generated in 5 minutes."""
    message = generate_report.send_with_options(
        args=("report_001", params),
        delay=300_000,  # 5 minutes
    )
    return {"job_id": message.message_id, "status": "scheduled"}
```

---

## Track status in your own database — minimal, then durable

The `Message` result lookup is intentionally narrow. The robust pattern is to **track job status in your own database**. Here is the minimal shape:

```python
# tasks.py — MINIMAL. Two bugs, both fixed below. Do not ship this.
import uuid

@dramatiq.actor(max_retries=3)
def process_document(job_id: str, doc_id: str):
    db.update_job(job_id, status="processing")
    try:
        result = do_processing(doc_id)
        db.update_job(job_id, status="completed", result=result)
    except Exception as e:
        db.update_job(job_id, status="failed", error=str(e))
        raise  # re-raise so Dramatiq's retry logic kicks in


# main.py
@app.post("/documents/{doc_id}/process")
async def start_processing(doc_id: str):
    job_id = str(uuid.uuid4())
    db.create_job(job_id, status="queued")   # write 1
    process_document.send(job_id, doc_id)    # write 2 — different system
    return {"job_id": job_id}
```

> **Why this beats the result backend:** you control the schema, can add timestamps, progress tracking, and user association, and the data survives a Redis flush.

Two things are wrong with it, and both are invisible in testing:

⚠️ **The submission is a dual write.** `db.create_job()` commits to Postgres, `.send()` publishes to Redis. Crash — or lose the Redis connection — between them and the job row exists in `queued` forever with no message behind it. The user's status endpoint says "queued" and no worker will ever touch it. There is no error anywhere.

⚠️ **The actor has no ownership.** `update_job(status="processing")` is unconditional, so a duplicate delivery (redelivery after a crash, or a lease-expiry reclaim) happily flips a `RUNNING` job that another worker is actively executing, and `update_job(status="completed")` from the *slow* worker later overwrites the fast one's result. Meanwhile the `except` branch writes `failed` and then re-raises for a retry, so a transient error makes the user-visible status flap `processing → failed → processing → failed` on the way to eventually succeeding.

### Fix 1 — one transaction for the job and the intent to publish

```sql
CREATE TABLE jobs (
    id               uuid PRIMARY KEY,
    doc_id           text        NOT NULL,
    state            text        NOT NULL,   -- PENDING | RUNNING | COMPLETED | FAILED
    attempts         int         NOT NULL DEFAULT 0,
    next_attempt_at  timestamptz,            -- retry backoff, NULL = eligible now
    attempt_token    uuid,                   -- the fence: unique per claim
    lease_expires_at timestamptz,
    result           jsonb,
    last_error       text
);

CREATE TABLE outbox (
    id           bigserial PRIMARY KEY,
    job_id       uuid        NOT NULL REFERENCES jobs (id),
    published_at timestamptz,                -- NULL = not yet on the broker
    UNIQUE (job_id)
);
CREATE INDEX outbox_unpublished ON outbox (id) WHERE published_at IS NULL;
```

```python
# main.py — the canonical endpoint for the rest of this note
import uuid
from fastapi import HTTPException

@app.post("/documents/{doc_id}/process", status_code=202)
async def start_processing(doc_id: str):
    job_id = str(uuid.uuid4())
    # ONE transaction. Either both rows exist or neither does; there is no
    # window where a job is queued with nothing to publish it.
    with db.begin() as tx:
        tx.execute(insert(jobs).values(id=job_id, doc_id=doc_id, state="PENDING"))
        tx.execute(insert(outbox).values(job_id=job_id))
    return {"job_id": job_id, "state": "PENDING"}


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, "state": job.state, "result": job.result}
```

Note what the endpoint no longer does: it does not call `.send()` at all. Publication is a separate, retryable step.

```python
# publisher.py — run as its own process, or a loop in the worker container
def publish_outbox_once(batch: int = 100) -> int:
    with db.begin() as tx:
        rows = tx.execute(
            select(outbox.c.id, outbox.c.job_id, jobs.c.doc_id)
            .join(jobs, jobs.c.id == outbox.c.job_id)
            .where(outbox.c.published_at.is_(None))
            .order_by(outbox.c.id)
            .limit(batch)
            # skip_locked: several publisher replicas can run without blocking
            # each other or double-claiming the same row.
            .with_for_update(of=outbox, skip_locked=True)
        ).all()

        for row in rows:
            # Publish INSIDE the transaction, mark published INSIDE the same one.
            # A crash after send() but before commit re-publishes on the next pass,
            # which is at-least-once — the actor's claim makes that harmless.
            # The reverse order (commit, then send) would be at-most-once: a lost
            # message with no record that it was lost.
            process_document.send(str(row.job_id), row.doc_id)
            tx.execute(
                update(outbox).where(outbox.c.id == row.id).values(published_at=func.now())
            )
    return len(rows)
```

**How you know the publisher is healthy:** the age of the oldest unpublished row stays near zero.

```sql
SELECT coalesce(max(now() - j.created_at), interval '0') AS oldest_unpublished
  FROM outbox o JOIN jobs j ON j.id = o.job_id
 WHERE o.published_at IS NULL;
```

⚠️ **The silent failure is a growing gap with everything else green.** Jobs get created, the API returns `202`, `/jobs/{id}` reports `PENDING` — and `oldest_unpublished` climbs because the publisher process is dead, wedged on a broker connection, or was never deployed. Nothing errors. Alert on that gauge, not on the API.

### Fix 2 — the actor claims an attempt, and only that attempt may finish it

```python
# tasks.py — the canonical actor for the rest of this note
import uuid
import dramatiq

from .broker import broker  # noqa: F401

LEASE_SECONDS = 60


@dramatiq.actor(max_retries=3, time_limit=300_000, notify_shutdown=True)
def process_document(job_id: str, doc_id: str):
    # A NEW token per attempt. worker_id is not enough: a restarted process
    # reuses its identity, so an old partitioned attempt could otherwise
    # complete work that a newer attempt now owns.
    token = str(uuid.uuid4())

    if not claim_job(job_id, token):
        # Lost the race. Either a duplicate delivery, another worker holds a
        # live lease, or the job is already terminal. Returning acks the
        # message: there is nothing left for THIS delivery to do.
        logger.info("claim_lost", extra={"job_id": job_id})
        return

    try:
        # job_id is the stable idempotency key across every retry of this job,
        # so a provider that already accepted the work returns the same answer.
        result = do_processing(doc_id, idempotency_key=job_id)
    except TransientError as exc:
        # Release the claim and schedule the next attempt. State goes back to
        # PENDING — it never passes through FAILED, so the user never sees a
        # flap on work that is still going to succeed.
        release_for_retry(job_id, token, exc)
        raise                       # let Dramatiq redeliver after its backoff
    except PermanentError as exc:
        fail_permanently(job_id, token, exc)
        return                      # do NOT re-raise: retrying cannot help

    complete_job(job_id, token, result)
```

The three helpers are the whole of the correctness, so here they are in SQL rather than behind method names:

```python
def claim_job(job_id: str, token: str) -> bool:
    """PENDING (due) or RUNNING-with-expired-lease -> RUNNING, owned by `token`."""
    with db.begin() as tx:
        r = tx.execute(text("""
            UPDATE jobs
               SET state            = 'RUNNING',
                   attempt_token    = :token,
                   lease_expires_at = now() + make_interval(secs => :lease),
                   attempts         = attempts + 1
             WHERE id = :job_id
               AND (
                     (state = 'RUNNING' AND lease_expires_at < now())
                  OR (state = 'PENDING'
                      AND (next_attempt_at IS NULL OR next_attempt_at <= now()))
               )
        """), {"job_id": job_id, "token": token, "lease": LEASE_SECONDS})
    return r.rowcount == 1          # 0 rows = someone else owns it, or it is terminal


def complete_job(job_id: str, token: str, result: dict) -> None:
    with db.begin() as tx:
        r = tx.execute(text("""
            UPDATE jobs
               SET state = 'COMPLETED', result = :result,
                   attempt_token = NULL, lease_expires_at = NULL
             WHERE id = :job_id AND attempt_token = :token AND state = 'RUNNING'
        """), {"job_id": job_id, "token": token, "result": Json(result)})
    if r.rowcount != 1:
        # Our lease expired mid-flight and another attempt took over. Our result
        # is stale and must NOT be written. Loud, because it means LEASE_SECONDS
        # is shorter than the real work.
        raise LeaseLost(job_id, token)


def release_for_retry(job_id: str, token: str, exc: Exception) -> None:
    delay = min(2 ** get_attempts(job_id), 300) * random.uniform(0.5, 1.5)
    with db.begin() as tx:
        tx.execute(text("""
            UPDATE jobs
               SET state = 'PENDING',
                   next_attempt_at = now() + make_interval(secs => :delay),
                   attempt_token = NULL, lease_expires_at = NULL,
                   last_error = :err
             WHERE id = :job_id AND attempt_token = :token AND state = 'RUNNING'
        """), {"job_id": job_id, "token": token, "delay": delay, "err": repr(exc)})


def fail_permanently(job_id: str, token: str, exc: Exception) -> None:
    with db.begin() as tx:
        tx.execute(text("""
            UPDATE jobs SET state = 'FAILED', last_error = :err,
                            attempt_token = NULL, lease_expires_at = NULL
             WHERE id = :job_id AND attempt_token = :token AND state = 'RUNNING'
        """), {"job_id": job_id, "token": token, "err": repr(exc)})
```

Every one of the four statements is conditional on `attempt_token = :token`. That single predicate is what makes duplicate delivery safe: the second delivery's `claim_job` matches zero rows and returns early, and a stale worker whose lease expired cannot complete, retry, or fail a job that now belongs to somebody else.

⚠️ **A `time_limit` longer than the lease is a bug.** With `time_limit=300_000` (5 minutes) and `LEASE_SECONDS = 60`, a job that legitimately runs for 90 seconds loses its lease at 60 s, gets reclaimed by a reconciler, and its original attempt then raises `LeaseLost` at the end — having already done the work. Either renew the lease from a heartbeat while the actor runs, or set `LEASE_SECONDS` above the actor's `time_limit`. The lease protocol, its heartbeat, and the reconciler that reclaims expired leases are in [leases, heartbeats, and fencing](../../reliability/02_leases_heartbeats_and_fencing.md).

---

## Request context crosses the process boundary only if you send it

Request context (request ID, user ID, trace ID) does **not** automatically propagate to Dramatiq workers — they are separate processes. Use custom middleware to pass it through message options.

### Define the Context

```python
# context.py
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="unknown")
user_id_var: ContextVar[str] = ContextVar("user_id", default="anonymous")
```

### Custom Middleware

```python
# middleware.py
import dramatiq
from .context import request_id_var, user_id_var

class ContextPropagationMiddleware(dramatiq.Middleware):
    """Propagate context variables from the sender to the worker."""

    def before_process_message(self, broker, message):
        """Restore context from message options (worker side)."""
        request_id = message.options.get("request_id", "unknown")
        user_id = message.options.get("user_id", "anonymous")
        request_id_var.set(request_id)
        user_id_var.set(user_id)

    def after_process_message(self, broker, message, *, result=None, exception=None):
        """Reset context after processing."""
        request_id_var.set("unknown")
        user_id_var.set("anonymous")
```

### Register the Middleware

Add one line to the existing `broker.py` — do **not** replace the file. The `Results` middleware configured earlier must stay, or `@dramatiq.actor(store_results=True)` in `tasks.py` fails at import with `ValueError: The following actor options are undefined: store_results`.

```python
# broker.py — addition, after the existing Results middleware
from .middleware import ContextPropagationMiddleware

broker.add_middleware(ContextPropagationMiddleware())
```

### Send Context with Messages

⚠️ Context goes in as **keyword arguments** to `send_with_options`, not inside an `options=` dict. `send_with_options` takes processing options as `**options`, so passing `options={...}` nests the whole dict under `message.options["options"]` — `message.options.get("request_id")` in the middleware then returns `None` and every worker log line reads `[unknown]` forever. Nothing raises, because option-name validation happens only in the `@dramatiq.actor` decorator, never on the send path.

```python
# main.py — direct send, when you have not adopted the outbox yet
import uuid
from .context import request_id_var, user_id_var

@app.post("/documents/{doc_id}/process", status_code=202)
async def start_processing(doc_id: str):
    job_id = str(uuid.uuid4())
    db.create_job(job_id, state="PENDING")
    process_document.send_with_options(
        args=(job_id, doc_id),
        # Correct: options as keyword arguments.
        request_id=request_id_var.get(),
        user_id=user_id_var.get(),
    )
    return {"job_id": job_id, "state": "PENDING"}
```

```python
# WRONG — silently propagates nothing.
process_document.send_with_options(
    args=(job_id, doc_id),
    options={"request_id": request_id_var.get()},
)
```

⚠️ **With the outbox, the endpoint no longer sends, so a ContextVar read at send time is empty.** The publisher runs in its own process with no request in flight, so `request_id_var.get()` there returns the default — every worker log line reads `[unknown]` and nothing errors. Persist the context on the outbox row inside the request's transaction and re-attach it when publishing:

```python
# ALTER TABLE outbox ADD COLUMN request_id text, ADD COLUMN user_id text;

# main.py — inside the same transaction as the jobs row
tx.execute(insert(outbox).values(
    job_id=job_id,
    request_id=request_id_var.get(),
    user_id=user_id_var.get(),
))

# publisher.py — the context travels with the row, not with the process
process_document.send_with_options(
    args=(str(row.job_id), row.doc_id),
    request_id=row.request_id,
    user_id=row.user_id,
)
```

Source: [`dramatiq/actor.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/actor.py), checked 2026-08-03.

### Use Context in Actors

```python
# tasks.py — the canonical actor, now reading propagated context
from .context import request_id_var

@dramatiq.actor(max_retries=3)
def process_document(job_id: str, doc_id: str):
    logger.info(f"[{request_id_var.get()}] Processing document {doc_id}")
    # The request_id was set by ContextPropagationMiddleware
```

This is the same pattern you'd use for trace IDs, tenant IDs, or any per-request state.

**How you know it worked**: a worker log line shows the same request ID the API logged for that request. If it shows `unknown` while the API's own logs show a real ID, the send side is wrong — check for `options=` nesting before suspecting the middleware.

---

## Async actors: opt in, or keep a separate loop per actor

A FastAPI codebase almost certainly has an async database session and an async HTTP client, so the first question is whether an actor can be `async def`. It can, via the opt-in `AsyncIO` middleware:

```python
# broker.py — addition
from dramatiq.middleware import AsyncIO

broker.add_middleware(AsyncIO())
```

```python
# tasks.py
@dramatiq.actor(max_retries=3)
async def process_document(job_id: str, doc_id: str):
    async with httpx.AsyncClient(timeout=20.0) as client:
        result = await run_pipeline(client, doc_id)
    await db.update_job(job_id, status="completed", result=result)
```

The middleware runs one event-loop thread per **worker** process. Two consequences that matter here:

- ⚠️ **Per-process concurrency is unchanged.** Each worker thread blocks waiting for its coroutine's result, so in-flight work per process is still the `--threads` count (8 by default). Async actors let you *use* async libraries; they do not give you an async worker's concurrency profile.
- The worker's loop is not FastAPI's loop, and must never be. They are different processes.

If you do not adopt the middleware, the alternative is an explicit `asyncio.run()` inside each actor:

```python
@dramatiq.actor(max_retries=3)
def process_document(job_id: str, doc_id: str):
    asyncio.run(_process_document(job_id, doc_id))   # fresh loop, per message
```

That works, but creates and tears down a loop — and therefore any loop-bound connection pool — on every message. Never reuse a module-level loop across actor invocations, and never share one with the web process.

Source: [reference docs](https://dramatiq.io/reference.html) and [`middleware/asyncio.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/middleware/asyncio.py), checked 2026-08-03.

---

## Test your actors, not Dramatiq

### The stub broker replaces Redis, but you must rebind the actors

Dramatiq ships a `StubBroker` that keeps everything in-memory — no Redis needed in tests.

```python
# tests/conftest.py
import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker
from dramatiq.results import Results
from dramatiq.results.backends import StubBackend


@pytest.fixture
def stub_broker(monkeypatch, db):
    """Replace the real broker with an in-memory stub, and rebind the app's own actors to it."""
    broker = StubBroker()
    broker.add_middleware(Results(backend=StubBackend()))
    broker.emit_after("process_boot")  # trigger middleware setup
    dramatiq.set_broker(broker)

    # Each Actor holds a reference to the broker it was bound to at import time.
    # Without re-pointing them, myapp.tasks still publishes to the real Redis broker
    # and the tests below assert nothing about the application's actors.
    import myapp.tasks as tasks

    for actor in (tasks.process_document, tasks.send_notification, tasks.generate_report):
        monkeypatch.setattr(actor, "broker", broker)
        broker.declare_actor(actor)

    # The broker is not the only module-level dependency the actors close over.
    # tasks.py resolved `db` at import time too, so without this the actor writes
    # to the real database while the test asserts against the fixture's — the
    # assertion fails with "job not found" and the broker looks innocent.
    monkeypatch.setattr(tasks, "db", db)

    yield broker
    broker.flush_all()
    broker.close()


@pytest.fixture
def stub_worker(stub_broker):
    """Start a worker that processes messages synchronously."""
    worker = dramatiq.Worker(stub_broker, worker_timeout=100)
    worker.start()
    yield worker
    worker.stop()
```

### Import the real actors

Test the **application's** actors, imported from `myapp.tasks`. An actor defined inside the test body only proves that Dramatiq works — it passes with the real actors completely broken.

```python
# tests/test_tasks.py
from myapp.tasks import process_document


def test_process_document_marks_job_completed(stub_broker, stub_worker, db):
    job_id = "job_1"
    db.create_job(job_id, state="PENDING")

    process_document.send(job_id, "doc_123")

    stub_broker.join(process_document.queue_name, timeout=10_000)
    stub_worker.join()

    assert db.get_job(job_id).state == "COMPLETED"


def test_duplicate_delivery_does_not_run_twice(stub_broker, stub_worker, db, monkeypatch):
    """The conditional claim is the only thing making redelivery safe — test it."""
    job_id = "job_2"
    db.create_job(job_id, state="PENDING")
    calls = []
    monkeypatch.setattr("myapp.tasks.do_processing",
                        lambda doc_id, idempotency_key: calls.append(doc_id) or {"ok": True})

    process_document.send(job_id, "doc_123")   # first delivery
    process_document.send(job_id, "doc_123")   # broker redelivered the same job
    stub_broker.join(process_document.queue_name, timeout=10_000)
    stub_worker.join()

    assert db.get_job(job_id).state == "COMPLETED"
    assert len(calls) == 1      # the second claim matched zero rows and returned
```

```python
# tests/test_tasks.py — retries, with a test actor because the point IS the retry
import dramatiq
import pytest


def test_task_retry_on_failure(stub_broker, stub_worker):
    calls = []

    # min_backoff must be set: the default is 15 s and doubles per retry, so this
    # test would block for roughly 45 seconds. The guide requires a value above 100 ms.
    @dramatiq.actor(max_retries=2, min_backoff=100, max_backoff=200)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("boom")

    stub_broker.declare_actor(flaky)

    flaky.send()
    # fail_fast defaults to True since 2.0.0 — this message fails twice on the way
    # to succeeding, so opt out or join() re-raises ConnectionError in the test thread.
    stub_broker.join(flaky.queue_name, timeout=10_000, fail_fast=False)
    stub_worker.join()

    assert len(calls) == 3  # 1 initial + 2 retries
```

`broker.join()` blocks until the queue is drained; `worker.join()` blocks until all threads finish. Two properties of `StubBroker.join` to know:

- ⚠️ **Since Dramatiq 2.0.0 it re-raises the failed message's exception in the test thread** — `fail_fast` now defaults to `True`. A test that deliberately asserts on a permanently-failed message must pass `fail_fast=False`, or it fails with the actor's own exception instead of reaching its assertion.
- **Always pass `timeout=`.** Without it, a wedged actor hangs the test run instead of failing it.

Source: [2.0.0 release notes](https://github.com/Bogdanp/dramatiq/releases/tag/v2.0.0) and [`brokers/stub.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/brokers/stub.py) (`fail_fast_default: bool = True`), checked 2026-08-03.

### Integration Tests (with Real Redis)

Requires `pytest-asyncio`. The fixture must use `@pytest_asyncio.fixture` — a plain `@pytest.fixture` on an `async def` generator is not awaited under pytest-asyncio's default strict mode, so the test receives an async generator object and errors. (Setting `asyncio_mode = auto` in your pytest config is the alternative.)

```python
# tests/test_integration.py
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from myapp.main import app


@pytest_asyncio.fixture      # not @pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_enqueue_document_processing(client):
    # No request body: the endpoint takes doc_id from the path and nothing else.
    response = await client.post("/documents/doc_123/process")
    assert response.status_code == 202          # matches status_code=202 on the route
    data = response.json()
    assert "job_id" in data
    assert data["state"] == "PENDING"           # the endpoint's two-key contract
```

⚠️ **This test asserts the response contract and nothing else.** With the outbox version of the endpoint it passes while the publisher is dead — that is the point of the `oldest_unpublished` gauge above. If you want the durable path covered, assert on the rows the request wrote:

```python
@pytest.mark.asyncio
async def test_enqueue_writes_job_and_outbox_atomically(client, db):
    job_id = (await client.post("/documents/doc_123/process")).json()["job_id"]

    assert db.get_job(job_id).state == "PENDING"
    # The outbox row is what guarantees the message will eventually be published.
    # Without this assertion the dual-write bug reappears and no test notices.
    assert db.get_outbox_row(job_id).published_at is None
```

---

## Same image, two commands

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:8-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  api:
    build: .
    command: uvicorn myapp.main:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    environment:
      - REDIS_URL=redis://redis:6379/0
      - REDIS_RESULT_URL=redis://redis:6379/1
    depends_on:
      redis:
        condition: service_healthy

  worker:
    build: .
    command: dramatiq myapp.tasks --processes 2 --threads 4
    environment:
      - REDIS_URL=redis://redis:6379/0
      - REDIS_RESULT_URL=redis://redis:6379/1
    depends_on:
      redis:
        condition: service_healthy
    stop_grace_period: 60s

volumes:
  redis-data:
```

**Same image, different command.** The API runs `uvicorn`, the worker runs `dramatiq`. They share the same codebase and dependencies.

Worker count is set with `--scale` at run time (see [Scaling Workers Independently](#3-workers-scale-independently-of-the-api) below), not with `deploy.replicas` in the file — declaring both means the two disagree and the file silently wins on `docker compose up` without `--scale`.

**How you know the worker booted.** `docker compose logs worker` shows one line from the main process and one per forked worker process — `--processes 2` gives two:

```
worker-1  | [2026-08-03 09:14:02,113] [PID 1] [MainThread] [dramatiq.MainProcess] [INFO] Dramatiq '2.2.0' is booting up.
worker-1  | [2026-08-03 09:14:02,486] [PID 7] [MainThread] [dramatiq.WorkerProcess(0)] [INFO] Worker process is ready for action.
worker-1  | [2026-08-03 09:14:02,491] [PID 8] [MainThread] [dramatiq.WorkerProcess(1)] [INFO] Worker process is ready for action.
```

⚠️ **`ready for action` is not evidence that the worker is consuming your queue.** There is no per-queue INFO line — queue binding is logged at `DEBUG` only. So a worker container can look perfectly healthy while bound to queues nothing sends to: `POST /documents/{id}/process` keeps returning `202`, the job row stays `PENDING` forever, and neither side logs an error. Two ways to actually check:

```bash
# 1. Ask the worker which queues it bound to (DEBUG, so -v is required).
docker compose run --rm worker dramatiq myapp.tasks -v --processes 1 2>&1 | grep 'Adding consumer'
# [...] [dramatiq.worker.Worker] [DEBUG] Adding consumer for queue 'default'.
# [...] [dramatiq.worker.Worker] [DEBUG] Adding consumer for delay queue 'default.DQ'.

# 2. Ask the broker whether anything is piling up unconsumed.
docker compose exec redis redis-cli llen dramatiq:low-priority
```

`generate_report` above uses `queue_name="low-priority"`, so a worker started with only `--queues default` never runs it and `llen dramatiq:low-priority` climbs. That growing length is the tell.

### Dockerfile

```dockerfile
FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command — overridden by docker-compose for api vs worker
CMD ["uvicorn", "myapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## What breaks in production

### 1. Graceful shutdown is partly opt-in

On `SIGTERM`, Dramatiq:
1. Stops consuming new messages
2. Waits for in-progress tasks to finish
3. Injects `dramatiq.middleware.Shutdown` (a subclass of `dramatiq.middleware.Interrupt`) into actors that **opted in** with `notify_shutdown=True`

⚠️ **Step 3 does not happen by default.** `ShutdownNotifications` defaults `notify_shutdown` to `False`, so `stop_grace_period: 60s` alone buys long tasks nothing but 60 seconds of running before `SIGKILL` — no exception is raised, no checkpoint code runs, and the task's progress is discarded silently. Opt in per actor:

```python
@dramatiq.actor(max_retries=3, notify_shutdown=True)
def process_document(job_id: str, doc_id: str):
    ...
```

Docker sends `SIGTERM` then waits `stop_grace_period` (default: 10s) before `SIGKILL`.

⚠️ **`time_limit=300_000` with `stop_grace_period: 60s` is a contradiction, and the compose file wins.** The actor is allowed 5 minutes of runtime, the container is destroyed after 1. A document that takes 90 seconds is killed at 60 seconds every single deploy — mid-flight, with its lease still held, so the job only moves again when the reconciler notices the expired lease. Two ways out, and you must pick one:

**Option A — make the grace period the real ceiling.** Simplest, correct when tasks are genuinely short:

```yaml
worker:
  stop_grace_period: 310s   # > the actor's time_limit of 300_000 ms, plus slack
```

**Option B — checkpoint and requeue on `Shutdown`.** Correct when tasks are long enough that you will not wait 5 minutes for a deploy. The actor releases its claim and lets the job be redelivered rather than dying holding it:

```python
from dramatiq.middleware import Shutdown

@dramatiq.actor(max_retries=3, time_limit=300_000, notify_shutdown=True)
def process_document(job_id: str, doc_id: str):
    token = str(uuid.uuid4())
    if not claim_job(job_id, token):
        return
    try:
        result = do_processing(doc_id, idempotency_key=job_id)
    except Shutdown:
        # The worker is going away in seconds. Hand the job back NOW: state
        # returns to PENDING with next_attempt_at = now, so the next worker
        # picks it up immediately instead of waiting out the lease.
        release_for_retry(job_id, token, Shutdown("worker shutting down"))
        raise                       # re-raise so Dramatiq requeues the message
    complete_job(job_id, token, result)
```

`Shutdown` subclasses `dramatiq.middleware.Interrupt`, which subclasses `BaseException` — so a bare `except Exception:` will **not** catch it, and the `try/except TransientError` in the canonical actor above lets it straight through. Catch it explicitly or not at all.

Source: [`middleware/shutdown.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/middleware/shutdown.py), checked 2026-08-03. The mechanism and its GIL caveat are in [the overview](overview.md#shutdown-notification-is-opt-in-per-actor).

### 2. Health Checks for Workers

Dramatiq workers don't expose HTTP by default. Options:

```python
# Option A: use the Prometheus middleware.
# Requires: pip install 'dramatiq[prometheus]' — prometheus_client is NOT part of
# dramatiq[redis], and this import raises ImportError without it.
from dramatiq.middleware.prometheus import Prometheus
broker.add_middleware(Prometheus())
```

It serves metrics on `0.0.0.0:9191` by default (`dramatiq_prom_host` / `dramatiq_prom_port` to override, `dramatiq_prom_db` for the multiprocess directory), which gives you a probe target:

```yaml
livenessProbe:
  httpGet:
    path: /metrics
    port: 9191
  initialDelaySeconds: 10
  periodSeconds: 30
```

**Option B: a heartbeat file whose *freshness* is the signal.** The naive version touches the file when a message is processed — but that makes liveness a function of traffic, so an idle worker looks dead and a wedged worker that processed one message an hour ago looks alive. Drive it from a thread that runs regardless of traffic:

```python
# health.py
import pathlib
import threading

import dramatiq

HEARTBEAT = pathlib.Path("/tmp/dramatiq-healthy")
HEARTBEAT_INTERVAL = 10.0        # seconds


class HeartbeatMiddleware(dramatiq.Middleware):
    """Touch a file every HEARTBEAT_INTERVAL, independent of message flow."""

    def after_worker_boot(self, broker, worker):
        self._stop = threading.Event()
        # daemon=True: never block interpreter exit on the heartbeat.
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def after_worker_shutdown(self, broker, worker):
        self._stop.set()
        # Remove the file so a stopped worker fails the probe immediately
        # instead of coasting on a stale-but-recent mtime.
        HEARTBEAT.unlink(missing_ok=True)

    def _loop(self):
        while not self._stop.wait(HEARTBEAT_INTERVAL):
            HEARTBEAT.touch()
```

The probe then checks **mtime against a threshold**, not existence:

```yaml
livenessProbe:
  exec:
    # Fail if the file has not been touched in the last minute — 6x the
    # heartbeat interval, so a couple of missed ticks do not restart the pod.
    command: ["sh", "-c", "[ -n \"$(find /tmp/dramatiq-healthy -mmin -1)\" ]"]
  initialDelaySeconds: 15
  periodSeconds: 20
```

⚠️ **`test -f` is not a liveness check.** The file exists from the moment the worker boots and keeps existing after every thread has deadlocked, after the broker connection has been lost, and after the process has stopped consuming entirely. A probe on existence alone passes forever and the pod is never restarted — which is worse than no probe, because it reads as coverage.

⚠️ **Liveness is not the same as "work is progressing."** A worker can heartbeat perfectly while every message it takes fails, or while it holds a lease on a job it can no longer finish. Pair the probe with the queue-depth and `oldest_unpublished` gauges above; only those tell you the *system* is moving.

### 3. Workers scale independently of the API

Workers and API servers scale independently:

```bash
# Scale workers based on queue depth
docker compose up --scale worker=5

# Or in Kubernetes
kubectl scale deployment dramatiq-worker --replicas=10
```

### 4. One worker per queue keeps urgent work moving

```yaml
# docker-compose.yml
worker-high:
  build: .
  command: dramatiq myapp.tasks --queues high-priority --processes 4 --threads 4

worker-default:
  build: .
  command: dramatiq myapp.tasks --queues default low-priority --processes 2 --threads 8
```

### 5. Permanent failures need an alert, not a log line

⚠️ **Do not hang this on `after_skip_message`.** That hook fires when middleware *skips* a message (`AgeLimit`, `SkipMessage`), not when retries run out. The `Retries` middleware calls `message.fail()` and lets the message be **nacked**, so an `after_skip_message` alerter is never called for the case you built it for — and it is silent, so it looks like you simply have no permanent failures.

Use the `on_retry_exhausted` actor option, which Dramatiq invokes with **two** arguments:

```python
# tasks.py
@dramatiq.actor
def alert_ops(message_data: dict, retry_data: dict):
    # message_data is Message.asdict(); retry_data is {"retries", "max_retries"}.
    send_alert(
        channel="task-failures",
        text=f"Task permanently failed: {message_data['actor_name']} "
             f"(id={message_data['message_id']}, args={message_data['args']}, "
             f"attempts={retry_data['retries']}/{retry_data['max_retries']})",
    )


@dramatiq.actor(max_retries=3, on_retry_exhausted=alert_ops.actor_name)
def process_document(job_id: str, doc_id: str):
    ...
```

⚠️ **A one-argument callback fails at exhaustion time, not at import time** — `TypeError: alert_ops() takes 1 positional argument but 2 were given`, raised inside the alerting actor, where nobody is watching for it.

For a cross-cutting alerter rather than a per-actor option, use `after_nack` and read the retry counters off the message:

```python
class AlertingMiddleware(dramatiq.Middleware):
    def after_nack(self, broker, message):
        retries = message.options.get("retries", 0)
        send_alert(
            channel="task-failures",
            text=f"Task permanently failed: {message.actor_name} "
                 f"(id={message.message_id}, args={message.args}, retries={retries})\n"
                 f"{message.options.get('traceback', '')}",
        )
```

**Verify it before you rely on it.** Set `max_retries=0` on one actor, send it a message that raises, and watch for both the WARNING and your alert:

```
[...] [dramatiq.worker.WorkerThread] [WARNING] Retries exceeded for message '<id>'.
```

⚠️ If the WARNING appears and your alert does not, the hook is wrong — that is exactly what the `after_skip_message` version looks like in production.

The mechanism and the `Retries` source path are in [the overview](overview.md#what-happens-after-max-retries).

### 6. The broker survives threads but not `fork()`

Everything above puts `broker.py` at import time, which is correct for `uvicorn myapp.main:app` — one process, one broker, one set of connections. It stops being correct the moment a pre-forking server loads your code *before* forking.

Dramatiq's builtin brokers are **thread-safe but not process-safe**. `fork()` is copy-on-write, so any file descriptor opened before the fork — including the broker's Redis socket — is *shared* by every child process rather than duplicated. Two children writing to one socket interleave their protocol bytes, and the symptom Dramatiq documents is a `FileNotFoundError` when enqueueing.

- **gunicorn: nothing to do.** It loads the application *after* forking by default, so each worker builds its own broker.
- **gunicorn with `--preload`: you have opted back into the bug.** Preloading loads your code before the fork, which is exactly the case above. Either drop `--preload`, or rebuild the broker connection in a `post_fork` hook.
- **uWSGI: turn on [lazy apps](https://uwsgi-docs.readthedocs.io/en/latest/Options.html#lazy-apps).** `lazy-apps = true` loads app code after each worker forks. The tradeoff is slightly higher memory, since nothing is shared copy-on-write any more.

The rule underneath all three: **open the broker's connections after the last fork**, never before.

⚠️ **This failure only appears under load and only in the forked deployment.** A single-process `uvicorn` dev server can never reproduce it, and the first child to enqueue often succeeds — so the error looks intermittent and gets blamed on Redis. If `FileNotFoundError` shows up on the `.send()` path in production and nowhere else, check your server's fork/load order before touching the broker config.

Source: [troubleshooting: FileNotFoundError when Enqueueing](https://dramatiq.io/troubleshooting.html), checked 2026-08-03.

---

## Mental Model

```
FastAPI (any instance)
    │
    │  ONE transaction: jobs row (PENDING) + outbox row     ← the only durable write
    ▼
Postgres ──────────────────────────────────────────┐
    │                                              │
    │  publisher reads unpublished outbox rows      │
    ▼                                              │
Redis Broker                                       │  claim / complete / retry,
    │                                              │  all conditional on the
    │  message sitting in queue (a HINT, not truth) │  attempt token
    ▼                                              │
Dramatiq Worker (separate process/container)       │
    │                                              │
    │  claim_job(job_id, new attempt token) ────────┤
    │  do_processing(idempotency_key=job_id)        │
    │  complete_job(job_id, same token) ────────────┘
    ▼
FastAPI (any instance) reads durable job status from Postgres
```

> The API and the worker share **code** (same Python package) but not **processes**. The API records intent; the publisher hands it to Redis; the worker claims an attempt and executes. Postgres is the source of truth and Redis is only the transport — which is why losing a message costs you latency rather than the job.

Use this integration when FastAPI is the command/API boundary and Dramatiq is already the chosen task runtime. Do not use the result backend as a cross-instance domain status API, enqueue before the domain transaction commits, or run critical actors inside the web process.

---

**Next**: [APScheduler](../apscheduler/overview.md)
