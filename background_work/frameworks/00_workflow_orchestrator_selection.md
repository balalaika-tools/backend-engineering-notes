# Workflow Orchestrators Replace Coordination Plumbing, Not Business Correctness

> **Who this is for**: Backend engineers deciding whether database jobs, a broker, or a durable workflow orchestrator should own a multi-step process.

Before reading this, separate state, delivery, and execution in **[Queue and Worker Architectures](../04_queue_and_worker_architectures.md)**.

---

## 1. Repeated waits and recovery rules turn application code into a runtime

A database-backed workflow starts clearly: one state row, one job table, a polling worker, and perhaps an outbox plus broker. Then the product adds approval waits, timers, parallel branches, child workflows, compensation, manual retry, and compatibility for runs created by old code. At that point the team spends more time maintaining coordination machinery than implementing business steps.

A **durable workflow orchestrator** owns that coordination lifecycle:

```text
command / signal
       │
       ▼
durable workflow history
       ├── next-step decision
       ├── durable timer or wait
       ├── retry schedule
       └── activity/task dispatch ──► worker or service
                                         │
                                         └──► domain DB / external provider
```

The orchestrator remembers where the process is and what should happen next. Workers still perform the real I/O or compute, and the domain database still owns business entities that do not belong in workflow history.

> **The near-miss**: an orchestrator is not a queue with a visual graph. A queue transports independent work; an orchestrator persists decisions between steps and decides which work becomes eligible next.

---

## 2. An engine can replace the custom control plane, not every application guarantee

The end-to-end database example hand-builds a small orchestrator. Its rows and loops have direct engine equivalents. Start with the **first and last rows**: they decide who owns progression and who protects irreversible effects.

| Custom DB + worker mechanism | Engine-owned equivalent | Still application-owned |
|---|---|---|
| **`workflow_runs` and transition history** | **Durable workflow/event history** | **Domain entities and business audit evidence** |
| `jobs`, attempt leases, and retry timestamps | Activity/task scheduling, timeouts, retries, and worker heartbeats | Provider capacity and error classification |
| Outbox hint and broker routing | Engine task queues or service integrations | Cross-system start boundary when a DB commit must also start a workflow |
| Reconciler for a missing next step | History recovery and replay | Reconciliation with an external provider after an ambiguous result |
| Cancellation transition and compensation job | Cancellation/signal handling and compensation branches | Authorization and the compensating business action |
| **Provider operation record and idempotency key** | **No automatic replacement** | **Exactly-once business-effect protection** |

With custom hybrid dispatch, the database owns both workflow progression and job ownership:

```text
API → domain DB + job + outbox → publisher → broker → worker → DB claim
```

With an engine, engine history normally owns orchestration and schedules activities:

```text
API/client → workflow engine history → activity task → worker → domain DB/provider
```

Do not keep the complete `workflow_runs` state machine and an engine state machine as co-equal authorities. A useful split is:

```text
engine history → orchestration position, waits, attempts, next step
domain DB      → orders, documents, approvals, money, provider evidence
```

Project an engine-owned status into the database when APIs need a read model, but make the projection rebuildable and document which side wins a disagreement.

> **Key insight**: Moving coordination into an engine changes who proves the next step is owed; it does not change the need to prove that an external business effect happened once.

---

## 3. Choose the product whose ownership model matches the workflow

For service workflows, start with the three **bold** rows. Airflow and LangGraph are specialized choices, not progressively more powerful versions of the same tool.

| Option | Coordination model | Use it when | Do not reach for it when |
|---|---|---|---|
| **Custom DB polling or DB + outbox + broker** | Application tables and reconcilers own progression | Steps are few and stable; volume is low/moderate; SQL visibility and transactional domain updates matter | Timers, signals, compensation, and migrations are becoming a platform |
| **AWS Step Functions Standard** | Managed Amazon States Language state machine with durable execution history | The system is AWS-native and needs long waits, service integrations, visual execution history, `.sync` jobs, or callback task tokens | The workflow must be cloud-portable or primarily expressed as application code |
| **Temporal** | Deterministic workflow code replayed from event history; activities perform I/O | Long-lived service workflows need code-first composition, signals, timers, child workflows, compensation, and frequent evolution | One job or a small fixed lifecycle already fits a job table clearly |
| AWS Step Functions Express | Short, high-volume state-machine executions | Idempotent event processing or short service orchestration needs managed scaling | Human waits, `.sync` jobs, callback task tokens, or executions longer than five minutes are required |
| Airflow | Scheduled DAG runs and data-interval history | Data pipelines need schedules, dependencies, backfills, and reruns by interval | Request-driven product workflows or interactive human approvals dominate |
| LangGraph | Checkpointed graph state and resumable nodes | Agent/LLM tool loops, interrupts, and graph-shaped state are the product | The process is a conventional service workflow with no agent graph |

AWS currently documents **Standard Workflows** as durable and auditable for up to one year, with exactly-once workflow execution unless `Retry` is configured. **Express Workflows** run for up to five minutes; asynchronous Express is at-least-once and synchronous Express is at-most-once. Express does not support `.sync` or `.waitForTaskToken`: [Choosing a Step Functions workflow type](https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html). Checked 2026-08-03.

Those execution labels describe the state-machine runtime, not an atomic transaction with a payment, email, database, or model provider. A configured retry can invoke a task again, and a remote effect can succeed before its response becomes durable. Keep external operations idempotent.

Temporal reconstructs coordination by replaying workflow history. Workflow code must remain deterministic; database, network, provider, and LLM calls belong in activities, which can be retried and therefore need idempotency: [Workflow Definition](https://docs.temporal.io/workflow-definition) and [Activities](https://docs.temporal.io/activities). Checked 2026-08-03.

See **[Temporal-Class Engines](temporal/overview.md)** for the code-first model. Azure Durable Functions and Google Cloud Workflows occupy adjacent cloud-orchestrator territory; evaluate them with the same ownership, replay, integration, limit, and lock-in questions rather than treating every managed state machine as interchangeable.

---

## 4. The editorial workflow exposes the architectural difference

For 10–50 generation jobs per day with a short, stable lifecycle, direct DB polling remains the default:

```text
approval transaction → workflow row + PENDING job
worker poll          → conditional claim + attempt token
worker               → provider call + durable result
reconciler           → expired-lease recovery
```

Adding SQS for burst handling changes only dispatch:

```text
approval transaction → workflow row + job + outbox
publisher             → SQS delivery hint
worker                → authoritative DB claim
```

Moving the workflow to Step Functions Standard changes the coordination owner:

```text
Step Functions execution
  → research task
  → callback wait with task token for approval
  → generation task
  → success / catch / compensation branch
```

Standard Workflows support Request Response, `.sync`, and `.waitForTaskToken` patterns. In the callback pattern, Step Functions can place a task token in an SQS message and remain suspended until an authorized component calls `SendTaskSuccess` or `SendTaskFailure`: [Step Functions integration patterns](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html). The callback receiver still authenticates the reviewer and stores durable approval evidence; possession of a task token is not business authorization.

Moving it to Temporal expresses the same progression as workflow code:

```text
Temporal workflow
  → research activity
  → durable wait for approve signal/update
  → generation activity with stable provider key
  → completion or compensation
```

The engine retains the wait with no worker process held open. Activity workers may disappear and return; history determines which activity is still owed.

---

## 5. Starting a workflow can recreate the same dual-write problem

An engine removes the application's internal `job → outbox → broker` handoff only when engine history is the commit point for orchestration. It cannot atomically share a transaction with an unrelated PostgreSQL database.

This still has a crash window:

```text
commit approval in domain DB
process crashes
start Step Functions / Temporal never happens
```

Choose one explicit boundary:

1. **Engine-first** — start with a stable workflow/execution ID, then let an idempotent activity write domain state.
2. **Database-first** — commit domain state plus a `workflow.start_requested` outbox row; a starter process invokes the engine idempotently and records the engine execution ID.
3. **Command-first** — send the command to the running workflow; an activity applies the domain transaction and reports the result back into history.

The second option is the closest migration from the hybrid example. The outbox remains, but it starts one workflow execution instead of delivering every individual job.

⚠️ If both the engine and `workflow_runs` can independently advance `WAITING_FOR_REVIEW → GENERATION_RUNNING`, recovery cannot determine which transition is authoritative.

⚠️ Built-in retry without a stable provider operation key can duplicate the exact external effect the engine was adopted to protect.

---

## 6. Coordination complexity—not job volume or duration—sets the threshold

Use this boundary:

```text
one independent result
  → durable job + worker

few stable business states, simple recovery
  → DB workflow + polling workers

same simple lifecycle, but large bursts or routing needs
  → DB workflow + outbox + broker

many durable timers/signals/branches/compensations/versioned long-lived runs
  → Step Functions Standard or Temporal

short high-volume idempotent state-machine executions on AWS
  → Step Functions Express

scheduled data dependencies and backfills
  → Airflow

agent/LLM graph state and human interrupts
  → LangGraph
```

A ten-hour independent job does not require an orchestrator merely because it is long. A low-volume approval process may justify one if it waits for weeks, changes definition frequently, and has several compensating branches. Conversely, 100,000 independent image conversions need queue capacity, not workflow history for each conversion.

**How you know the choice is working**: one system can answer which step is owed, why it is eligible, how it will resume after process loss, and which operation key protects its next external effect. A rising count of custom timer tables, repair scripts, and special-case reconcilers is evidence that a database workflow is crossing the engine threshold.

Do not adopt an orchestrator only for a visual diagram, built-in retries, or the word “workflow.” For the 10–50-jobs-per-day case in the end-to-end example, keep DB polling until coordination requirements—not anticipated scale—make the engine-owned mechanisms necessary.

---

**Next**: [Temporal-Class Engines](temporal/overview.md)
