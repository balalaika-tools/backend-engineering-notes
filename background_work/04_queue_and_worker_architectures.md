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

## 2. One pending job has three possible durable homes

Follow one unit of work, `job-55`, before comparing product features. Each architecture must preserve the same facts; it assigns their ownership differently.

| Moment | Database polling | Broker plus outbox | Managed queue |
|---|---|---|---|
| API commits intent | `jobs(job-55, PENDING)` commits beside domain state | `jobs` and `outbox(job-55, unpublished)` commit beside domain state | Domain job/outbox commits before cross-system send |
| Work becomes visible | A worker query finds the ready job row | Publisher sends an ID-only hint; broker stores the message | Sender places an ID-only message in the queue |
| Worker takes ownership | Conditional row claim creates a lease/token | Broker delivery wakes the worker; the database claim remains authoritative | Visibility receipt leases delivery; a DB token may also fence domain writes |
| Worker finishes | Fenced job update commits `SUCCEEDED` | Fenced job update commits, then delivery is acknowledged | Durable effect/state commits, then the receipt is deleted |
| Owner disappears | Expired row lease makes the job reclaimable | Message redelivers and/or expired DB lease is reclaimed | Visibility expires and the message redelivers |

For database polling, the first row is the whole handoff:

```text
API transaction                    worker transaction
───────────────                    ──────────────────
domain state changes               claim job-55 if PENDING
job-55 = PENDING      ───────────► job-55 = RUNNING + token-a
                                   complete only with token-a
```

This removes the database/broker dual write because business state and pending work share one commit. It fits low-to-moderate volume when the domain database is authoritative and the team is willing to operate polling, ready-row indexes, leases, retries, cleanup, and fairness.

The comparison note does not own the claim protocol. Use [Leases, Heartbeats, and Fencing](reliability/02_leases_heartbeats_and_fencing.md) for the complete `SKIP LOCKED` claim, heartbeat, token replacement, terminal-write, and recovery SQL. A worker claims only its real free slots; a semaphore around already leased rows merely hides lease hoarding in local memory.

**How you know it fits**: ready-row query latency and database load stay bounded, while oldest-ready age falls when worker capacity increases. Rising age with idle workers points to the polling index, readiness predicate, lease recovery, or admission policy—not automatically to insufficient CPU.

Do not use database polling when queue traffic would dominate the primary database, consumers require independent retention/replay, or the team does not want to own queue-runtime behavior.

---

## 3. A broker transports a hint; the outbox preserves the database handoff

A broker is useful for routing and wake-up, but it cannot join the domain transaction. The outbox keeps PostgreSQL as the commit point:

```text
1. API transaction commits:
   workflow = RESEARCH_QUEUED
   job-55   = PENDING
   outbox-9 = UNPUBLISHED(job_id=job-55)

2. Publisher later sends outbox-9:
   broker message = {message_id: outbox-9, job_id: job-55}

3. Worker receives the hint:
   reload job-55 from PostgreSQL
   claim it only if its state and version are still eligible
```

If the API dies after step 1, the publisher still finds `outbox-9`. If the publisher dies after the broker accepted step 2 but before marking it published, it sends the same message ID again. Duplicate publication is deliberate; the worker converges on the authoritative job claim.

The full table schema, dependent state/job/outbox CTE, publish claim, confirmation mark, failure backoff, and reconciliation implications belong to [Atomic Transitions and Outbox](reliability/01_atomic_transitions_and_outbox.md). The architecture-level contract is shorter:

- The domain transition and outbox row commit together.
- The publisher marks delivery only after the broker confirms it.
- Consumers reload authority by ID and tolerate the same message more than once.

Broker durability settings still matter. A durable RabbitMQ queue (normally a replicated [quorum queue](https://www.rabbitmq.com/docs/quorum-queues)), persistent messages, and publisher confirms answer different loss windows; one does not imply the others. [Redis-backed Celery transports](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html) emulate acknowledgement with a visibility timeout, so a task that runs past that timeout can be delivered again while the first worker is still active. Size the transport window from measured runtime or move long work to a transport/runtime with renewable ownership.

**How you know it fits**: domain transition rate, oldest-unpublished age, broker confirmation failures, message redelivery, and oldest-ready job age form one explainable chain. Healthy API commits beside steadily rising oldest-unpublished age mean the publisher handoff is failing even if broker queue depth is zero.

Use this architecture when a broker and worker ecosystem already exist, routing matters, or database polling is the wrong load shape. Do not use it to hide an unstable multi-step workflow: branches, human waits, durable timers, compensation, and versioned replay are signals to evaluate a workflow engine.

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

A durable workflow engine persists workflow history, durable timers, signals, pause/resume points, activity attempts, and recovery. It is an alternative owner for workflow coordination, not another layer that every database queue or broker architecture should add. Use one when those mechanisms are central to the product rather than incidental infrastructure.

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
| General durable workflow engine | Long-lived service workflows, timers, signals, compensation, replay | Temporal, AWS Step Functions Standard |
| Checkpointed graph runtime | Agent/LLM graphs, interrupts, persisted graph state, tool loops | LangGraph |

LangGraph’s [official persistence guide](https://docs.langchain.com/oss/python/langgraph/persistence) describes snapshots saved at graph steps and resume from the last successful step. This is not the same job as a generic task queue.

Activities and graph nodes may execute again after a crash or replay. Pass stable idempotency keys to payments, email providers, webhook receivers, and LLM/provider calls where supported; otherwise record the provider request and result around the side effect.

Adopting an engine changes this responsibility boundary:

```text
custom design: workflow rows + jobs + outbox + reconciler own coordination
engine design: engine history + activity tasks own coordination
both designs: domain DB and idempotency records own business evidence
```

Do not layer an engine over an existing authoritative state machine and let both own the same transitions. Either make the engine history authoritative for orchestration while the database owns domain entities, or keep coordination in the database and use simpler workers. If committing a domain row must also start an engine execution, the cross-system dual write still needs an outbox, stable execution ID, or an engine-first transaction boundary.

Use Step Functions Standard or Temporal when durable waits, signals, compensation, child workflows, and definition evolution are numerous enough that custom timer tables and reconcilers are becoming a runtime. Keep DB polling or hybrid dispatch for a few stable steps. Compare the products, their execution models, and the migration boundary in **[Workflow Orchestrator Selection](frameworks/00_workflow_orchestrator_selection.md)**.

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
