# Background Work Is Several Separate Systems

> **Who this is for**: Backend engineers choosing how to run work after an API request or on a schedule.

---

## 1. Start with the responsibility, not the framework

A request returns, but document generation still needs ten minutes. The first design question is not “Celery or Dramatiq?” It is which responsibilities the system must provide: durable state, pending-work delivery, execution, timing, or multi-step coordination.

For a simple email, a queue and a worker may be enough. For a workflow that pauses for approval and resumes tomorrow, the system also needs persistent workflow state and a transition protocol. Adding a scheduler does not create either one.

> **The near-miss**: “background task,” “long-running task,” and “workflow” sound like sizes of the same thing. Duration is only one dimension. A two-second payment workflow may need durable business state; a two-hour idempotent image conversion may still be one task.

---

## 2. Ten concepts cover different failure boundaries

Start with the **six core responsibilities** in bold; the remaining four concepts refine them when the problem needs them.

| Concept                      | Responsibility                                                        | Durable by itself?                  |
| ---------------------------- | --------------------------------------------------------------------- | ----------------------------------- |
| **Background task**    | One unit of work run outside the initiating request                   | No                                  |
| Long-running task            | A task whose duration affects timeouts, shutdown, leases, or progress | No                                  |
| **Stateful workflow**  | Several steps whose progress and decisions must survive restarts      | Only with persistent state          |
| State machine                | Defines allowed workflow states and transitions                       | No; it is a model                   |
| Job/task record              | Records one execution request, attempt, status, and error             | Yes, if persisted                   |
| **Queue or broker**    | Holds and delivers pending work to consumers                          | Only if configured for it           |
| **Worker**             | Claims work and executes a step                                       | No                                  |
| **Scheduler**          | Decides when a job or workflow should be created                      | Depends on its store                |
| **Workflow engine**    | Persists and coordinates steps, timers, signals, retries, or history  | Normally yes                        |
| Human-in-the-loop checkpoint | Pauses before a decision or side effect and resumes with input        | Only with persistent workflow state |

A framework may cover several rows, but the rows do not collapse into one. Celery and Dramatiq provide task publication and worker runtimes; APScheduler focuses on timing; a durable workflow engine coordinates persisted progress.

"Only if configured for it" is the row that most often surprises people, so it is worth naming the three concrete cases:

- **RabbitMQ** — durability is opt-in and needs all three of a durable (quorum) queue, messages published as persistent, and publisher confirms. Miss any one and a broker restart or a failed publish loses work silently.
- **Redis-backed transports** — Redis has no native acknowledgement, so these emulate it with a *visibility timeout*: a claimed message becomes visible again after a fixed period. Work that outlives the timeout is redelivered while the first worker is still running it, and unclean shutdowns can drop it entirely.
- **Amazon SQS** — durable and replicated by default; the thing you configure is the visibility timeout, not the durability.

Sources: [RabbitMQ quorum queues](https://www.rabbitmq.com/docs/quorum-queues), [Celery Redis broker notes](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html) (checked 2026-08-03). [Queue and Worker Architectures](04_queue_and_worker_architectures.md) §4 works through the consequences.

> **Key insight**: Queue state answers “what delivery is pending?” Workflow state answers “what does the business process mean now?” One cannot safely stand in for the other.

---

## 3. Keep three state domains separate

Suppose an approval workflow is generating a report. At one moment it can have all three of these states:

```text
Business/workflow state: WAITING_FOR_HUMAN_REVIEW
Task execution state:    generation attempt 2 = FAILED
Queue-delivery state:    retry message = VISIBLE
```

| State domain      | Typical values                                                      | Authoritative owner                |
| ----------------- | ------------------------------------------------------------------- | ---------------------------------- |
| Business/workflow | `NEW`, `WAITING_FOR_HUMAN_REVIEW`, `COMPLETED`, `CANCELLED`  | Domain database or workflow engine |
| Task execution    | `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, attempt number | Job table or task runtime          |
| Queue delivery    | ready, leased/in-flight, acknowledged, redelivered, dead-lettered   | Queue or broker                    |

An acknowledged message does not prove that a payment was recorded. A `COMPLETED` workflow does not prove that every old duplicate message has disappeared. Define a contract for how workers translate delivery events into atomic business transitions.

---

## 4. The normal data flow uses IDs, not copied authority

```text
Client ──POST /runs──► API / publisher
                         │
                         ├──transaction──► domain DB: workflow + job/outbox
                         │
                         └──publish──────► queue/broker: {run_id, step_id, expected_version}
                                                    │
                                                    ▼
                                                Worker
                                                    │
                                                    ├──load current state──► DB
                                                    ├──perform side effect
                                                    └──conditional update─► DB
```

The message should usually carry identifiers, an idempotency key, and the expected state or version. It should not carry a stale copy of the entire authoritative domain object. The worker reloads current state and refuses work whose precondition no longer holds.

Success is visible as a traceable chain from `workflow_run_id` to `step_id`, job attempt, message ID, worker, and resulting transition. A task that runs but cannot be correlated back to a durable record is only partially observable.

---

## 5. Choose durability independently from concurrency

Two decisions are often accidentally tied together:

1. **How work is delivered** — in-process background hook, database job table, broker, managed queue, or workflow engine.
2. **How work executes** — process, thread, coroutine, external compute service, or a mixture.

A managed queue can feed native `asyncio` workers. A database-backed queue can feed process workers. Celery can use prefork or threads. The transport does not decide whether a workload is CPU-bound or I/O-bound.

See [Task Execution Models](05_task_execution_models.md) for the execution decision and [Queue and Worker Architectures](04_queue_and_worker_architectures.md) for the delivery decision.

---

## 6. Escalate only when a simpler boundary fails

The first four rows are the normal starting points; engines and orchestrators are for workflows whose persisted coordination justifies another platform.

| Need                                                             | Smallest reasonable mechanism                      |
| ---------------------------------------------------------------- | -------------------------------------------------- |
| Best-effort post-response work                                   | In-process background hook                         |
| Durable independent task                                         | Queue or job table plus worker                     |
| Periodic creation of work                                        | Scheduler creates an idempotent job                |
| Several fixed stages                                             | Persistent domain state plus jobs; broker optional |
| Long-lived timers, pause/resume, approval, compensation, history | Durable workflow engine or checkpointed graph      |
| Data pipeline with scheduled dependencies and backfills          | Data orchestrator such as Airflow                  |

⚠️ In-process background work disappears when the web process exits and competes with request handling. Use it only when losing the work is acceptable.

⚠️ A broker retry can repeat an external side effect. Delivery guarantees become business guarantees only after idempotency and atomic state transitions are added.

Do not introduce a queue merely to move a cheap, non-critical operation out of the request. Do not introduce a workflow engine for one independent idempotent task. Each extra runtime adds deployment, monitoring, and recovery work.

---

**Next**: [Part 2: When a Task Becomes a Workflow](02_when_a_task_becomes_a_workflow.md)
