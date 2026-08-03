# Celery Provides Mature Task Infrastructure, Not Business Workflow State

> **Who this is for**: Python engineers evaluating Celery for durable background tasks and fixed-stage pipelines.

Before reading this, understand **[queue and worker architectures](../../04_queue_and_worker_architectures.md)** and **[task execution models](../../05_task_execution_models.md)**.

Celery’s current stable documentation is the [5.6 user guide](https://docs.celeryq.dev/en/stable/userguide/).

---

## 1. What this framework is

A signup endpoint has to send a welcome email, and the SMTP provider is having a slow morning. Doing it inline makes every signup wait four seconds; doing it in a thread means a deploy loses whichever emails were in flight. You need the work to outlive the request, survive a restart, and retry on its own — and you do not want to write serialization, delivery, and worker lifecycle yourself.

That is Celery's job. It saves a team from building generic task plumbing: serialization, publication through a broker, queue routing, acknowledgements, worker lifecycle, execution pools, retries, delayed/periodic publication, monitoring events, and optional result storage.

> **The near-miss**: `pip install celery` feels like installing a queue. It isn't. Celery is a client library plus a worker runtime that both talk to **a broker you must run and operate yourself** — RabbitMQ, Redis, or another Kombu transport. The install supplies neither the queue nor any durability. This is why §3 starts with a broker URL: that is not boilerplate, it is the actual dependency, and its configuration is what determines whether your tasks survive anything.

```text
producer/API ──task message──► broker ──delivery──► Celery worker
                                  │                    │
                                  │                    └── process/thread/greenlet pool
                                  │
                                  └── pending delivery state

Celery worker ──optional task result──► result backend
Celery worker ──business updates──────► domain database
```

| Role | Celery coverage |
|---|---|
| Scheduler | Celery Beat publishes periodic tasks |
| Task queue | Yes, through broker transports |
| Worker runtime | Yes |
| Broker abstraction | Yes, through Kombu transports |
| Result backend abstraction | Yes, optional |
| Durable business-workflow engine | No |

> **Key insight**: Celery owns task delivery and execution state; the application still owns authoritative business state and replay-safe side effects.

---

## 2. What this framework is not

Celery Canvas can compose chains, groups, and chords, but that does not turn `AsyncResult` into a durable domain state machine. Result backends have retention, failure, and cleanup semantics optimized for task results. They should not decide whether an order is approved, a payment is final, or a workflow may resume.

Celery also does not make a database commit and broker publish atomic. It cannot guarantee an external side effect runs exactly once, infer compensation, or provide human approval semantics by itself.

Use persistent domain state plus an outbox for fixed workflows. Use a durable workflow engine when long-lived timers, signals, replay, compensation, and human checkpoints dominate the design.

---

## 3. The smallest working task proves the wiring

Install Celery with the broker extra appropriate to the deployment. A minimal Redis example:

```python
# tasks.py — baseline for local verification, not production policy.
from celery import Celery

app = Celery(
    "background_work",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1",
)

@app.task
def add(left: int, right: int) -> int:
    return left + right
```

```bash
celery -A tasks worker --loglevel=INFO
```

In another process:

```python
from tasks import add

result = add.delay(2, 3)
print(result.get(timeout=10))
```

Success is `5` plus worker logs showing the task was received and succeeded. If the producer hangs or reports a broker connection error, verify the broker URL and connectivity. If the worker never registers `tasks.add`, its module was not imported by the Celery app.

Do not block on `result.get()` in a normal API request; return a domain `job_id` and expose status from the application database.

---

## 4. A hardened task carries IDs and explicit failure policy

```python
import uuid

from celery import Celery
from httpx import Client, HTTPStatusError, Timeout, TransportError

app = Celery("background_work")
app.config_from_object("worker_settings")

@app.task(
    bind=True,
    # TransportError, not TimeoutException: connect failures, DNS errors, and TLS
    # errors are TransportError siblings of TimeoutException, not subclasses, so
    # naming only TimeoutException turns the most common transient network fault
    # into a permanent task failure.
    autoretry_for=(TransportError, HTTPStatusError),  # Retries timeout, connect, 429, and 5xx after permanent 4xx is handled.
    retry_backoff=True,                 # Prevents immediate retry storms.
    retry_jitter=True,                  # Desynchronizes many failing workers.
    retry_kwargs={"max_retries": 5},  # Bounds the retry budget.
    acks_late=True,                     # Enables redelivery after abrupt worker loss.
    reject_on_worker_lost=True,         # Requests redelivery when a child disappears.
)
def generate_report(self, workflow_run_id: str, step_id: str, idempotency_key: str) -> None:
    # A NEW token for every attempt, including every autoretry. This is the fence:
    # only the attempt holding it may complete, fail, or release the step.
    attempt_token = uuid.uuid4().hex

    run = repository.load_run(workflow_run_id)
    if not repository.claim_step(run.id, step_id, run.version, attempt_token):
        return  # A stale or duplicate delivery cannot advance state.

    try:
        with Client(timeout=Timeout(connect=3, read=30, write=10, pool=2)) as client:
            response = client.post(
                "https://provider.example/reports",
                json={"workflow_run_id": workflow_run_id},
                headers={"Idempotency-Key": idempotency_key},
            )
            try:
                response.raise_for_status()
            except HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500 and exc.response.status_code != 429:
                    repository.fail_permanently(run.id, step_id, attempt_token, str(exc))
                    return  # Validation/auth failures do not improve with retries.
                raise
    except (TransportError, HTTPStatusError):
        # Release the claim BEFORE the exception reaches autoretry_for. Without
        # this the step stays RUNNING under a dead token, the retry's claim_step()
        # matches zero rows, and the task returns "successfully" having done
        # nothing — a permanently stuck workflow with a green task history.
        repository.release_step_for_retry(run.id, step_id, attempt_token)
        raise

    repository.complete_step_if_owned(
        workflow_run_id=run.id,
        step_id=step_id,
        attempt_token=attempt_token,   # completion must prove it still owns the step
        provider_result_id=response.json()["id"],
    )
```

⚠️ **`autoretry_for` does not know about your domain state.** It sees an exception, schedules a redelivery, and marks the task `RETRY` — the workflow row is entirely Celery's blind spot. Every path out of the task body must leave the step either terminal or releasable, or the retry cannot re-acquire what the previous attempt abandoned. The three places above are the complete set: permanent failure, transient release, success.

⚠️ **`acks_late=True` plus a hard worker loss skips your `except` blocks entirely.** `SIGKILL`, OOM, or a node dying runs no Python at all, so the step stays `RUNNING` with a live-looking token forever. Only a **time-bounded lease** plus a reconciler that reclaims expired ones recovers that case, which is why `claim_step` below matches on lease expiry rather than on status alone.

The two conditional statements that make this safe:

```sql
-- claim_step: acquire the step for THIS attempt, or match zero rows.
UPDATE workflow_steps
   SET state = 'RUNNING',
       attempt_token    = :attempt_token,
       lease_expires_at = now() + interval '60 seconds',
       attempts         = attempts + 1
 WHERE workflow_run_id = :run_id
   AND step_id         = :step_id
   AND run_version     = :expected_version      -- optimistic check on the run
   AND (state = 'PENDING'
        OR (state = 'RUNNING' AND lease_expires_at < now()));  -- reclaim abandoned

-- complete_step_if_owned: succeed only if we still hold the same attempt.
UPDATE workflow_steps
   SET state = 'COMPLETED', provider_result_id = :provider_result_id,
       attempt_token = NULL, lease_expires_at = NULL
 WHERE workflow_run_id = :run_id
   AND step_id         = :step_id
   AND attempt_token   = :attempt_token         -- the fence
   AND state           = 'RUNNING';
```

Both must assert exactly one affected row at the call site. A zero-row `complete_step_if_owned` means the lease expired mid-flight and another attempt owns the step — the result in hand is stale and must not be written.

`release_step_for_retry` is the mirror image: `RUNNING → PENDING` conditional on the same token, clearing the lease and setting `next_attempt_at`. The full lease protocol — heartbeat cadence, choosing the lease duration against the task's real runtime, and the reconciler — is in [leases, heartbeats, and fencing](../../reliability/02_leases_heartbeats_and_fencing.md).

---

## 5. Broker, result backend, and domain database have different jobs

| Component | Stores | Do not use it as |
|---|---|---|
| Broker | Pending/reserved task messages | Authoritative workflow state |
| Result backend | Task return value and runtime status | Permanent business record |
| Domain database | Workflow/job state, idempotency, audit, result references | High-throughput message broker by accident |

Messages should contain stable IDs, expected version/state, and an idempotency key. Load mutable context from the domain database inside the worker.

Enable a result backend only when a caller or Canvas primitive genuinely needs a task result. Otherwise set `task_ignore_result=True` or ignore results per task to avoid storage and cleanup cost.

⚠️ `AsyncResult.state == "SUCCESS"` means Celery recorded a successful task return. It does not prove a separate business transaction committed unless the task code enforces that contract.

---

## 6. Pool choice follows the workload

Celery 5.6’s [official concurrency guide](https://docs.celeryq.dev/en/stable/userguide/concurrency/) lists prefork, threads, eventlet, gevent, solo, and custom pools. It recommends prefork as the starting point and warns that switching pools can disable features such as soft timeouts and child recycling.

| Pool | Good fit | Main caveat |
|---|---|---|
| **prefork** | CPU-bound and general synchronous work | Process memory; initialize clients per child |
| **threads** | Blocking I/O with synchronous clients | GIL for Python CPU; stuck threads require provider timeouts |
| gevent/eventlet | Cooperative I/O with compatible libraries | Monkey-patching and missing pool features |
| solo | Debugging or strictly serial work | No parallel execution |
| custom | A deliberately maintained pool integration | Team owns compatibility and lifecycle |

The default subset is **prefork** and, for clearly blocking I/O, **threads**. The stable built-in pool list does not include a native `asyncio` pool. For high native-async concurrency, a custom async worker over a durable transport is often clearer than hiding an event loop inside synchronous task execution.

Example workers:

```bash
# CPU/general synchronous work: start near the container CPU allocation.
celery -A app worker -Q cpu --pool=prefork --concurrency=4

# Blocking I/O: tune against connection pools and provider quotas.
celery -A app worker -Q blocking_io --pool=threads --concurrency=32
```

---

## 7. Acknowledgement, retry, and redelivery are different

| Mechanism | Trigger | Creates a new attempt? | Purpose |
|---|---|:---:|---|
| Early acknowledgement | Before execution | No | Avoid redelivery of started tasks; crash can lose work |
| Late acknowledgement | After execution | Possibly | Permit redelivery after abrupt loss; requires idempotency |
| `self.retry()` / autoretry | Task code or exception policy | Yes | Retry a recognized transient failure |
| Broker redelivery | Unacknowledged delivery returns | Yes | Recover from worker/broker failure |

Celery’s [task API](https://docs.celeryq.dev/en/stable/reference/celery.app.task.html) documents that `acks_late` acknowledges after execution and that a task may then execute twice after a worker crash.

⚠️ **On the Redis broker, "unacknowledged delivery returns" is driven by a timer, not by worker loss.** Redis has no native acknowledgement, so Celery emulates it with a `visibility_timeout` that **defaults to one hour**. Any task still unacknowledged after that window is redelivered *while the original worker is healthy and still working on it* — and the Celery docs state such a task "will be executed again, and again in a loop." The same applies to any task whose ETA or countdown exceeds the timeout.

This is exactly the shape of §4's `generate_report`: a long provider call, `acks_late=True`, and therefore no acknowledgement until it finishes. On Redis with defaults, a report that takes 70 minutes is not a slow report — it is an infinite loop of duplicate reports. Three remedies, in order of preference:

```python
# worker_settings.py — raise the window above the longest task on this broker
broker_transport_options = {"visibility_timeout": 6 * 3600}   # 6 hours
```

- **Raise `visibility_timeout` above your longest task**, remembering it must also exceed the longest ETA/countdown you schedule.
- **Enable worker soft shutdown** so unacknowledged messages are re-queued deliberately on exit rather than waiting out the timer.
- **Route long tasks to RabbitMQ**, where acknowledgement is real and tied to the connection rather than to a clock.

Source: [Celery Redis broker documentation](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html), checked 2026-08-03. The framework-neutral version of this trade-off is in [queue and worker architectures](../../04_queue_and_worker_architectures.md#3-a-broker-needs-an-outbox-at-the-database-boundary) §3.

An ordinary task exception is not the same as worker loss. Configure retry only for transient exceptions; validation and authorization errors should become permanent failures. Configure `task_reject_on_worker_lost` only after verifying poison-task behavior, because repeated child crashes can create a destructive loop.

⚠️ Late acknowledgement without idempotency moves the failure from “possibly lost” to “possibly duplicated.” It does not create exactly-once execution.

---

## 8. Prefetch controls fairness and memory, not task concurrency

Celery reserves messages ahead of free execution slots. The [official optimization guide](https://docs.celeryq.dev/en/stable/userguide/optimizing.html) defines the prefetch count as `worker_prefetch_multiplier × concurrency` and recommends separating long and short tasks onto differently configured workers.

For long tasks, a common starting point is:

```python
# worker_settings.py
worker_prefetch_multiplier = 1
task_acks_late = True
```

With early acknowledgements, multiplier `1` can still mean one executing plus one reserved message per slot. With late acknowledgement it can restrict unacknowledged reservations to the concurrency slots. Measure broker-specific behavior.

High prefetch can improve throughput for tiny tasks by keeping work local. It can also make one worker hoard long tasks, increase memory, and hide queue depth from autoscaling. Route short and long tasks separately instead of searching for one universal multiplier.

---

## 9. Named queues isolate workloads and quotas

```python
# worker_settings.py
task_default_queue = "default"
task_routes = {
    "app.tasks.generate_report": {"queue": "cpu"},
    "app.tasks.call_provider": {"queue": "blocking_io"},
    "app.tasks.send_email": {"queue": "notifications"},
}
```

```bash
celery -A app worker -Q cpu --pool=prefork --concurrency=4
celery -A app worker -Q blocking_io --pool=threads --concurrency=32
celery -A app worker -Q notifications --pool=prefork --concurrency=8
```

The [routing guide](https://docs.celeryq.dev/en/stable/userguide/routing.html) documents named queues and transport-specific routing differences. Do not assume RabbitMQ exchanges, Redis priorities, and managed-queue transports behave identically.

Success is visible as each worker consuming only its declared queues and each queue responding independently to scale or downstream throttling. A CPU queue draining while provider calls starve indicates routing or worker configuration, not a global lack of workers.

---

## 10. Beat schedules publication, not exactly-once execution

Celery Beat publishes periodic task messages. Run one logical scheduler per schedule; Celery’s [periodic-task guide](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html) warns that multiple schedulers create duplicates.

```python
from celery.schedules import crontab

beat_schedule = {
    "nightly-cleanup": {
        "task": "app.tasks.cleanup",
        "schedule": crontab(hour=3, minute=0),
        "kwargs": {"schedule_key": "nightly-cleanup"},
    }
}
```

The task turns that into a durable job under a unique constraint and no-ops if the row already exists. Beat firing once is not the business guarantee — the unique row is.

⚠️ **`schedule_key` alone is not a firing identity, and deriving the date inside the task moves the bug rather than fixing it.** `f"{schedule_key}:{date.today()}"` is computed on the *worker*, at *delivery* time — so a message that sits in the queue past midnight, or is redelivered the next morning after a worker loss, derives a different key and runs the same nightly cleanup twice. Worse, two workers processing a duplicate publication either side of midnight both succeed. The firing identity has to come from the component that decided the firing happened:

```python
# beat_scheduler.py
import copy

from celery.beat import PersistentScheduler


class StampedScheduler(PersistentScheduler):
    """Stamp every published message with the slot beat fired it for."""

    def apply_async(self, entry, producer=None, advance=True, **kwargs):
        entry = self.reserve(entry) if advance else entry
        # Truncated to the minute — crontab's own granularity — so one entry can
        # never produce two slots for one firing, and a redelivery six hours
        # later still carries the ORIGINAL slot.
        slot = entry.schedule.now().replace(second=0, microsecond=0)
        entry = copy.copy(entry)
        entry.kwargs = {**entry.kwargs, "firing_slot": slot.isoformat()}
        # advance=False: we already reserved above; reserving twice skips a firing.
        return super().apply_async(entry, producer=producer, advance=False, **kwargs)
```

```bash
celery -A app beat --scheduler beat_scheduler:StampedScheduler
```

```python
# app/tasks.py
@app.task
def cleanup(schedule_key: str, firing_slot: str) -> None:
    # firing_slot came from beat, so every delivery and redelivery of this
    # firing agrees on the key no matter when the worker gets to it.
    if not repository.claim_firing(f"{schedule_key}:{firing_slot}"):
        return  # already claimed by an earlier delivery
    do_cleanup()
```

`claim_firing` is an `INSERT ... ON CONFLICT DO NOTHING` against a table with `UNIQUE (firing_key)`, returning whether a row was actually inserted.

**How you know it worked:** exactly one row per scheduled slot in that table, and `firing_slot` values that land on the schedule (`03:00`) rather than on whenever the worker happened to run.

⚠️ Verify against your Celery version before relying on it — `apply_async` and `reserve` are `celery.beat.Scheduler` internals, not a documented extension point. Checked against [`celery/beat.py` v5.5.3](https://github.com/celery/celery/blob/v5.5.3/celery/beat.py). If you would rather not subclass, publish from your own scheduler process instead and put the slot in the message yourself.

Use APScheduler when timing is needed without the rest of Celery. Use engine timers when the timer is part of one durable workflow’s state.

---

## 11. The outbox closes Celery’s database/broker gap

This is unsafe:

```python
repository.mark_queued(run_id)
generate_report.delay(run_id)  # A crash between the writes loses the task.
```

Instead, commit workflow state and an outbox row together. A publisher sends the Celery task with a stable `task_id`/message ID, then marks the outbox row published. Publication may happen more than once, so the worker’s application-level idempotency key remains authoritative.

If eventual publication is unacceptable and one database can carry the workload, a transactional database job table may be simpler than Celery. Celery’s advantage is mature generic task infrastructure, not a guarantee that database workers cannot implement similar semantics.

---

## 12. Monitoring needs business metrics beside worker events

Celery events, logs, queue inspection, and tools such as Flower expose worker/task activity. Also collect:

- Oldest-message age and queue depth by named queue.
- Task runtime, retry, worker-loss, and rejection rate.
- Child restarts, memory high-water, and broker reconnects.
- Domain workflow latency, stuck-state age, and reconciliation findings.
- Provider latency, rate limits, and idempotency conflicts.

Worker “online” is not enough. A registered worker can consume the wrong queue, block on a downstream pool, or repeatedly execute stale messages.

---

## 13. The broker is an unauthenticated code-execution path

§3 wires a bare `redis://localhost:6379/0`, which is fine on a laptop and dangerous anywhere else. Celery's [security guide](https://docs.celeryq.dev/en/stable/userguide/security.html) is blunt about the threat model: "by default, workers trust that the data they get from the broker hasn't been tampered with." A worker reads a task name and arguments off the queue and calls that task. So anyone who can write to your broker can make your workers run any registered task with any arguments.

The three things to get right:

- **Never expose the broker.** Private network only, credentials required, TLS in transit. This is the control that actually matters; the rest are defence in depth.
- **Sign your messages** with the `auth` serializer if the broker is shared or reachable by anything you do not control:

  ```python
  # worker_settings.py
  app.setup_security()          # requires cert/key config; rejects unsigned messages
  task_serializer = "auth"
  accept_content = ["auth"]
  ```

  ⚠️ `auth` **signs but does not encrypt**. Anyone who can read the queue still reads every argument in plaintext, so keep secrets and personal data out of task payloads and pass IDs instead.
- **Never accept `pickle`.** Leave `accept_content` restricted to `json` (the default) or `auth`; a broker that accepts pickled payloads turns queue write access into arbitrary code execution.

---

## 14. Testing Celery means testing through a worker

Celery ships `task_always_eager`, which runs tasks inline in the caller. It is convenient and it is explicitly not the recommended testing tool — the docs state it is "by definition not suitable for unit tests" because it *emulates* execution rather than executing through a worker, so it exercises none of the serialization, routing, acknowledgement, or retry behavior that actually breaks.

Use the pytest plugin instead. `celery.contrib.pytest` (or the standalone `pytest-celery` package) provides `celery_app` and `celery_worker` fixtures that start a real worker in-process:

```python
# conftest.py
pytest_plugins = ("celery.contrib.pytest",)

# test_tasks.py
def test_add_runs_through_a_worker(celery_app, celery_worker):
    @celery_app.task
    def add(a, b):
        return a + b

    celery_worker.reload()          # register the task with the running worker
    assert add.delay(2, 3).get(timeout=10) == 5
```

⚠️ **That test proves Celery works, not that your code does.** A task defined inside the test body passes with `generate_report` completely broken. The task you actually need covered is the §4 one, and covering it means registering the *application's* task against the fixture's app:

```python
# test_generate_report.py
import pytest
from httpx import TransportError

from app.tasks import generate_report      # the real §4 task


@pytest.fixture
def registered(celery_app, celery_worker):
    # The app instance under test is not the fixture's app, so the fixture's
    # worker cannot see the task. Register the existing task object on it, then
    # reload — without reload() the worker keeps its old registry and .get()
    # times out with no error that mentions registration at all.
    celery_app.register_task(generate_report)
    celery_worker.reload()
    return celery_worker


def test_generate_report_completes_the_step(registered, repository, provider):
    run = repository.create_run(state="PENDING")
    provider.will_return({"id": "prov_1"})

    # .delay() forces the real path: JSON-serialize the args, route to a queue,
    # publish, consume in the worker, acknowledge. task_always_eager skips all four.
    generate_report.delay(run.id, "report", idempotency_key=run.id).get(timeout=10)

    step = repository.load_step(run.id, "report")
    assert step.state == "COMPLETED"
    assert step.provider_result_id == "prov_1"
    assert step.attempt_token is None          # the claim was released on success


def test_transient_failure_releases_the_claim_then_succeeds(registered, repository, provider):
    run = repository.create_run(state="PENDING")
    provider.will_raise(TransportError("connection reset"), times=1)
    provider.will_return({"id": "prov_1"})

    generate_report.delay(run.id, "report", idempotency_key=run.id).get(timeout=30)

    step = repository.load_step(run.id, "report")
    assert step.state == "COMPLETED"
    # The retry had to re-acquire the step. Two attempts means the first one
    # released it — the stuck-workflow bug from §4 fails here with attempts == 1
    # and a step still sitting in RUNNING.
    assert step.attempts == 2
    assert provider.call_count == 2
```

The second test is the one worth having: it is the only place the release-on-retry path is exercised, and its failure mode without that path is a task Celery reports as `SUCCESS`.

⚠️ Set `retry_backoff` low (or override `retry_kwargs` for the test app) before asserting on a retry — the production backoff makes the second test wait tens of seconds and then fail on `timeout=30` rather than on the assertion.

Source: [Celery testing guide](https://docs.celeryq.dev/en/stable/userguide/testing.html), checked 2026-08-03.

---

## 15. When to use Celery

- The system needs mature Python task routing, worker pools, acknowledgements, retries, delayed tasks, and monitoring.
- The team already operates a supported broker and understands Celery failure semantics.
- Tasks are independent or workflows have a small, stable set of stages.
- Prefork or synchronous I/O pools fit the workload.

---

## 16. When not to use Celery

- One database job table would meet the volume and atomicity needs with less infrastructure.
- High-concurrency native async I/O is the primary execution model and a custom async poller is acceptable.
- The real requirement is a long-lived business workflow with human signals, durable timers, replay, or compensation.
- The workload is a **scheduled dependency graph** — datasets that must be built in order, with per-interval run history and the ability to re-run a past window. Reach for a data orchestrator; see [Airflow](../airflow/overview.md), whose own boundary section points back here for the case where you need a broker-backed worker fleet without DAG scheduling.
- The team cannot operate the broker, worker fleet, scheduler, result retention, DLQ/recovery, and outbox path.

⚠️ Canvas graphs can become an implicit workflow that is hard to reconcile with domain state. Keep business progression explicit or move it to a durable workflow engine.

---

**Next**: [Dramatiq](../dramatiq/overview.md)
