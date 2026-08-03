# Queue and Worker Architectures Trade Infrastructure for Failure Semantics

> **Who this is for**: Engineers choosing how pending work reaches workers after the authoritative state has been written.

Before reading this, understand **[workflow, task, and delivery state](01_overview.md#3-keep-three-state-domains-separate)**.

---

## 1. Pick the failure boundary you are prepared to operate

The API has committed `RESEARCH_QUEUED`. Now a worker must eventually run research. The architecture is determined by where pending work lives and how the system recovers if a process dies between “record intent” and “deliver work.”

The most common starting points are marked **default**.

| Architecture | Pending work lives in | Main advantage | Main operational burden | Default? |
|---|---|---|---|:---:|
| Database job table | Domain database | State transition and job creation share a transaction | You implement queue runtime behavior | ✓ for low/medium volume workflows |
| Database + broker + workers | Broker, with domain state in DB | Mature routing and worker ecosystem | Database/broker dual write | ✓ when broker infrastructure exists |
| Managed queue + custom pollers | Cloud queue service | Durable delivery without broker operations | Provider semantics and custom workers | ✓ in a matching cloud |
| Durable workflow engine | Engine history/store | Timers, signals, resume, and step coordination | New platform and programming model | |
| Checkpointed graph | Graph checkpointer | Agent/LLM state, interrupts, resume | Side-effect discipline and checkpoint operations | |
| Event choreography | Event log or broker topics | Independent consumers and loose coupling | End-to-end flow is harder to see | |
| In-process background hook | Web process memory | Almost no infrastructure | Work is lost on process exit | |
| Direct process/thread pool | Calling service memory | Local parallel execution | No durable delivery or independent scaling | |

The queue choice and execution pool choice are separate. Any durable transport can feed process, thread, or coroutine workers.

> **Key insight**: The best queue is not the one with the most features; it is the one whose failure boundary matches the system of record and the team’s recovery procedures.

---

## 2. A database job table removes the dual write

When workflow state and pending work share one relational database, the API can update both atomically:

```text
API transaction
  ├── workflow_runs: WAITING_FOR_HUMAN_REVIEW → GENERATION_QUEUED
  ├── jobs: insert PENDING generation job
  └── workflow_transitions: append approval event
                         │
                         ▼
workers poll and atomically claim jobs
```

PostgreSQL workers can claim rows without waiting on work held by other workers. The [PostgreSQL `SELECT` documentation](https://www.postgresql.org/docs/current/sql-select.html) notes that `SKIP LOCKED` gives an inconsistent view and is suitable for queue-like access, not general reporting queries.

The caller supplies `:available_slots`, calculated from actual free execution capacity. A worker with ten slots never claims fifty jobs and renews forty leases while they wait in local memory.

```sql
WITH candidates AS (
    SELECT id
    FROM jobs
    WHERE status = 'PENDING'
      AND next_attempt_at <= now()
    ORDER BY priority DESC, next_attempt_at, created_at
    FOR UPDATE SKIP LOCKED
    LIMIT :available_slots
)
UPDATE jobs AS j
SET status = 'RUNNING',
    attempt = attempt + 1,
    attempt_token = gen_random_uuid(),
    worker_id = :worker_id,
    lease_expires_at = now() + interval '90 seconds',
    started_at = COALESCE(started_at, now())
FROM candidates
WHERE j.id = candidates.id
RETURNING j.*;
```

Each claim gets a new `attempt_token`; `worker_id` is only observability metadata. Heartbeat, completion, failure, and recovery all predicate on the token. If a heartbeat fails or affects zero rows, execution is cancelled and the attempt is fenced. The complete ownership protocol belongs in [Leases, Heartbeats, and Fencing](reliability/02_leases_heartbeats_and_fencing.md), while durable failure transitions belong in [Retries, Timeouts, and Cancellation](reliability/04_retries_timeouts_and_cancellation.md).

For native async I/O, either claim exactly the free slot count or place unclaimed delivery IDs in a bounded local queue and claim them only as slots open. A semaphore around already-claimed work does not provide backpressure: the database lease is already being hoarded.

**Use it when** throughput is low to moderate, the database is already authoritative, atomic job creation matters, and the team can own leases, retries, prioritization, cleanup, and fairness.

**Do not use it when** queue traffic would dominate the primary database, independent consumers need different retention/replay semantics, or the team does not want to maintain a worker runtime.

Success signals are low claim-query latency, bounded polling query volume, stable database lock time, and queue age that falls when workers scale out. A rising oldest-job age while CPU is idle usually means an index, claim filter, or lease-recovery defect.

---

## 3. A broker needs an outbox at the database boundary

In a domain-database + broker architecture, responsibilities are explicit:

```text
API / publisher ──writes──► domain database
       │
       └──publishes IDs───► broker ──delivers──► worker
                                                   │
                                                   └──loads/updates──► domain database
```

The broker does not poll or understand the domain database. The API, an outbox publisher, or change-data-capture process writes broker messages. The worker receives a message, loads authoritative state by ID, checks the expected version/state, and conditionally updates the database.

**Nothing about installing a broker makes delivery durable.** Before this architecture is safer than the job table, three things must be true of the transport, framework-neutrally:

1. **The queue survives a broker restart** — on RabbitMQ that means a durable queue, and in practice a [quorum queue](https://www.rabbitmq.com/docs/quorum-queues), which replicates to a majority of nodes.
2. **Messages survive it too** — published as persistent. A persistent message on a transient queue is still lost.
3. **The publisher learns whether the broker took it** — publisher confirms. Without them a publish is fire-and-forget, and the outbox below cannot tell a delivered message from a dropped one.

⚠️ Redis-backed transports do not have acknowledgement at all; they emulate it with a visibility timeout, and [Celery's Redis documentation](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html) states that a task whose ETA or runtime exceeds `visibility_timeout` — one hour by default — "will be executed again, and again in a loop." The original worker is healthy and still working the whole time. Size the timeout above your longest task, or keep long tasks off a Redis transport. (Checked 2026-08-03.)

Publishing directly next to the transaction creates two losing timelines:

```text
Timeline A                            Timeline B
DB commit succeeds                   broker publish succeeds
process crashes                      DB transaction rolls back
broker publish never happens         worker sees an entity that does not exist
```

The **transactional outbox** makes the database the commit point. The table needs a publish-state column, because the publisher's whole job is to find rows it has not published yet:

```sql
CREATE TABLE outbox (
    id UUID PRIMARY KEY,                  -- also the broker message ID
    aggregate_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,             -- NULL = still owed to the broker
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claim_token UUID,
    publisher_id TEXT,
    claim_expires_at TIMESTAMPTZ,
    last_error TEXT,
    last_confirm_error TEXT
);

CREATE INDEX outbox_unpublished_idx
    ON outbox (next_attempt_at, created_at)
    WHERE published_at IS NULL;
```

```sql
-- The outbox insert receives a row only when the compare-and-set wins.
WITH updated AS (
    UPDATE workflow_runs
    SET state = 'RESEARCH_QUEUED',
        version = version + 1,
        updated_at = now()
    WHERE id = :run_id
      AND state = 'NEW'
      AND version = :expected_version
    RETURNING id, version
),
queued AS (
    INSERT INTO outbox (id, aggregate_id, event_type, payload)
    SELECT :message_id,
           id,
           'research.requested',
           jsonb_build_object(
               'workflow_run_id', id,
               'step_id', :step_id,
               'expected_version', version,
               'idempotency_key', :idempotency_key
           )
    FROM updated
    ON CONFLICT (id) DO NOTHING
)
SELECT id, version FROM updated;
```

Zero final rows mean zero state transition and zero outbox row. Treat that as a conflict; do not publish anything.

The publisher claims a batch the same way a worker claims jobs, publishes each row, then marks it published:

```sql
-- Claim
WITH candidates AS (
    SELECT id
    FROM outbox
    WHERE published_at IS NULL
      AND next_attempt_at <= now()
      AND (claim_expires_at IS NULL OR claim_expires_at <= now())
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 100
)
UPDATE outbox AS o
SET claim_token = gen_random_uuid(),
    publisher_id = :publisher_id,
    claim_expires_at = now() + interval '30 seconds',
    attempts = attempts + 1
FROM candidates
WHERE o.id = candidates.id
RETURNING o.id, o.event_type, o.payload, o.claim_token;

-- Mark, once the broker has confirmed the publish
UPDATE outbox
SET published_at = now(),
    claim_token = NULL,
    publisher_id = NULL,
    claim_expires_at = NULL,
    last_error = NULL,
    last_confirm_error = NULL
WHERE id = :outbox_id
  AND published_at IS NULL
  AND claim_token = :claim_token
RETURNING id;

-- On publish or confirm failure, release this claim onto a durable backoff.
UPDATE outbox
SET next_attempt_at = now() + (:backoff_seconds * interval '1 second'),
    last_error = :publish_error,
    last_confirm_error = :confirm_error,
    claim_token = NULL,
    publisher_id = NULL,
    claim_expires_at = NULL
WHERE id = :outbox_id
  AND published_at IS NULL
  AND claim_token = :claim_token
RETURNING id;
```

If the publisher crashes after publish but before the second statement, the claim expires and it publishes again. That is deliberate **at-least-once publication** — and it is why step 3 above matters: mark the row only after the broker confirms, never before. Consumers need an inbox/deduplication record or an idempotent claim keyed by `message_id` or `idempotency_key`.

Operational success is visible in **oldest unpublished age**, unpublished count, publish-confirm failure rate, attempts per row, and publisher throughput. A domain transition rate that remains healthy while oldest unpublished age rises is the silent dual-write symptom the outbox was meant to expose.

Change data capture can stream committed outbox rows instead of polling. It changes the publisher mechanism, not the duplicate-delivery contract. A reconciler should still detect a run stuck in `*_QUEUED` with no published outbox evidence.

This architecture fits independent jobs or a small, stable number of stages when a broker and task framework already exist. It starts becoming a hand-built workflow engine when branches, human waits, durable timers, compensation, replay, and dynamic tool execution accumulate.

---

## 4. A managed queue leases messages with a visibility timeout

Managed queues such as Amazon SQS remove broker operations but not worker correctness. The portable lifecycle is:

```text
producer sends ID-only message
          │
worker long-polls
          │
message becomes invisible / leased
          │
worker renews visibility while running
          ├── success → delete / acknowledge
          └── crash   → visibility expires → another delivery
                                      │
                               repeated failures
                                      ▼
                                     DLQ
```

AWS documents that an SQS message remains in the queue while temporarily invisible, becomes visible again if it is not deleted, and can still be delivered more than once under its at-least-once model; see [SQS visibility timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html).

The worker contract therefore requires the following. **The first three cannot be skipped by any implementation** — get them wrong and the queue silently duplicates or loses business effects. The rest are scale-up work you can add once traffic justifies it.

- **Set initial visibility above normal processing time** but below the acceptable recovery delay.
- **Delete only after durable effects and state transitions commit.** Deleting first turns a crash into lost work.
- **Make every side effect replay-safe.** At-least-once delivery is a promise you receive, not one you can decline.
- Extend visibility with a heartbeat for variable-duration work.
- Move poison messages to a DLQ after a deliberate receive count, then provide an inspected redrive procedure.
- Store large artifacts in object storage and send references. SQS accepts up to **1 MiB** per message (raised from 256 KiB in [August 2025](https://aws.amazon.com/about-aws/whats-new/2025/08/amazon-sqs-max-payload-size-1mib/)); past that you need S3 offload via the extended client library.
- Autoscale using both queue depth and oldest-message age; depth alone hides starvation.

⚠️ **The heartbeat has a hard ceiling.** An SQS message's visibility timeout cannot be extended beyond **12 hours from first receipt**, and each extension does not reset that clock. A job that runs longer than 12 hours is redelivered while the original worker is still running it — the same failure shape as the Redis `visibility_timeout` loop above, just with a longer fuse. The escape hatch is structural: split the work into steps that each finish well inside the window, or hand the long-lived coordination to a workflow engine (§5). Source: [SQS visibility timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html), checked 2026-08-03.

⚠️ **A DLQ does not reset the retention clock.** For standard queues a message keeps its *original* enqueue timestamp when it is moved to the DLQ, so it expires on the source queue's retention period, measured from when it was first sent — not from when it was dead-lettered. Set the DLQ's retention longer than the source queue's, or the poison messages you were preserving for inspection are deleted out from under you with no event anywhere. Source: [SQS DLQ documentation](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html), checked 2026-08-03.

One quota bounds the autoscaling advice above: a standard queue allows roughly **120,000 in-flight messages** (received but not yet deleted). Past it, short polling returns an `OverLimit` error and long polling simply stops returning messages — so a fleet that scales out on depth can stall with a deep queue and idle workers, which looks identical to a claim-filter bug.

FIFO ordering reduces parallelism because a message group serializes work. Use per-entity grouping only where ordering is a business requirement. A DLQ can also break strict end-to-end order; the SQS DLQ documentation calls this out for FIFO workloads.

---

## 5. Durable engines own coordination, not external side effects

A durable workflow engine persists workflow history, durable timers, signals, pause/resume points, activity attempts, and recovery. Use one when those mechanisms are central to the product rather than incidental infrastructure.

```text
workflow history / checkpoints
  ├── step A completed
  ├── timer scheduled until Friday
  ├── approval signal received
  ├── step B attempt 1 failed
  └── step B attempt 2 pending
```

Two subcategories matter:

| Category | Good fit | Examples |
|---|---|---|
| General durable workflow engine | Long-lived service workflows, timers, signals, compensation, replay | Temporal, cloud workflow services |
| Checkpointed graph runtime | Agent/LLM graphs, interrupts, persisted graph state, tool loops | LangGraph |

LangGraph’s [official persistence guide](https://docs.langchain.com/oss/python/langgraph/persistence) describes snapshots saved at graph steps and resume from the last successful step. This is not the same job as a generic task queue.

Activities and graph nodes may execute again after a crash or replay. Pass stable idempotency keys to payments, email providers, webhook receivers, and LLM/provider calls where supported; otherwise record the provider request and result around the side effect.

Do not layer an engine over an existing authoritative state machine and let both own the same transitions. Either make the engine history authoritative for orchestration while the database owns domain entities, or keep coordination in the database and use simpler workers.

---

## 6. Event choreography trades central control for independence

In choreography, services react to domain events without a central workflow controller:

```text
OrderPlaced
  ├──► Inventory reserves ──► InventoryReserved
  │                                └──► Payment charges ──► PaymentCharged
  └──► Analytics records event
```

This works well when consumers are genuinely independent and eventual consistency is acceptable. Each consumer must handle duplicate and out-of-order events, version its contract, and persist its own idempotent reaction.

For a saga, compensating events reverse earlier business effects. Compensation is a new action, not a database rollback: a refund can offset a charge, but an email cannot be unsent.

⚠️ If an engineer must reconstruct one user-visible workflow by searching events across five services, the decoupling has hidden the product’s state machine. Add explicit orchestration or a read model that makes progress visible.

Use choreography for independent reactions. Do not use it to disguise a tightly ordered process that needs one owner, global deadlines, or human checkpoints.

---

## 7. In-process work is intentionally non-durable

Web-server background hooks, local thread pools, and local process pools are acceptable when the caller can safely lose the work or reproduce it later.

| Mechanism | Acceptable use | Lost on restart | Main risk |
|---|---|:---:|---|
| In-process background hook | Best-effort audit enrichment or notification | Yes | Competes with requests |
| Thread pool | Bounded blocking I/O inside one service | Yes | Stuck threads and shared resource pressure |
| Process pool | Bounded local CPU work | Yes | Memory and shutdown complexity |

⚠️ Returning `202 Accepted` implies the server accepted responsibility for eventual processing. Do not return it for memory-only work unless the API contract explicitly permits loss.

⚠️ A persistent scheduler store preserves the schedule, not necessarily the currently executing side effect. Create an idempotent durable job at each firing when execution matters.

For concrete client-notification and callback patterns after work is submitted, continue to [Long-running task patterns](../architecture/long_running_tasks/README.md).

---

**Next**: [Part 5: Task Execution Models](05_task_execution_models.md)
