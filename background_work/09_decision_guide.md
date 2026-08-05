# Choose the Smallest System That Meets the Recovery Contract

> **Who this is for**: Engineers turning workload, durability, workflow, and operational constraints into a concrete background-work design.

Before choosing a tool, read **[the responsibility model](01_overview.md)**. First-time readers can use §§1–5 after building the **[minimal durable task](03_minimal_durable_task.md)**; read the **[reliability](reliability/README.md)** and **[production operations](operations/README.md)** sections before treating the choice as production-ready.

---

## 1. Ask four questions in order

Framework comparisons are noisy when the recovery contract is undefined. Use this sequence:

```text
1. Can the work be lost when this process exits?
   ├── yes → in-process background hook or local pool may be enough
   │
   └── no  → durable job/queue required
        │
        └── 2. Does progress contain business decisions across steps or time?
             ├── no  → task queue/job table + worker          ← stop here
             │
             └── yes → persistent workflow state
                  │
                  └── 3. Does it need durable timers, signals, pause/resume,
                         compensation, or replay?
                       ├── no  → database state + jobs is enough
                       └── yes → durable workflow engine / checkpointed graph

── every path above then answers ──

4. What does execution wait for?
   ├── CPU → processes/specialized compute
   ├── blocking I/O → bounded threads
   └── async I/O → coroutines + semaphore
```

Question 3 only applies if question 2 answered *yes*: there is no point asking about durable timers for a workload that has no persistent workflow state to attach them to. Question 4 applies to every path, including the non-durable one — delivery and execution are independent choices.

Only after these questions should you compare frameworks and managed services.

> **Key insight**: Tool selection is the last decision: first choose the source of truth, recovery boundary, and execution model, then select software that implements them.

### The four questions on a real workload

**The workload**: every night, generate a PDF usage report for each of 400 tenants and email it to the tenant's billing contact.

**1. Can the work be lost when this process exits?** No. A tenant who does not receive their report has no way to know one was owed, so a silent loss surfaces weeks later as a support ticket. → *durable job/queue required*. This rejects the in-process background hook: a deploy during the nightly window would drop whatever was in flight, and 03:00 rollouts are exactly when nobody is watching.

**2. Does progress contain business decisions across steps or time?** No. The steps — query the warehouse, render the PDF, upload it, send the email — are a fixed pipeline with no approval, no branching, and no waiting on anything external. A failed run can be discarded and re-run from the beginning. → *task queue/job table + worker*, and **question 3 does not apply**.

The tempting mistake here is to reach for workflow state because the pipeline has four steps. Four steps is not four business decisions. What the workload does need is an idempotency key per tenant per date (`report:{tenant_id}:2026-08-03`), so a retry after the email step does not send a second copy — that is [business-effect idempotency](reliability/03_idempotency_and_external_effects.md), not a state machine.

**4. What does execution wait for?** Mostly CPU: the warehouse query is a few seconds of I/O, the PDF render is tens of seconds of pure Python. → *processes*.

**The answer**: §2's **"Simple CPU-heavy task"** row — job record as state owner, database job table for delivery, process workers for execution — with §5's **"Pure Python CPU"** row for sizing: processes near the container's CPU allocation, scaled until CPU saturates or memory does. A scheduler creates 400 idempotent job rows at 03:00 using the [durable firing contract](06_scheduling_and_periodic_work.md) and does nothing else; it never runs the reports itself.

Two rows deliberately rejected: **Airflow**, because there is no dependency graph between datasets and nobody has asked for backfills — the scheduler here only needs to create work, and Airflow's cost is a platform to operate. **A durable workflow engine**, because nothing in the workload waits on a timer, a signal, or a human. If next quarter adds "hold the report for finance approval before sending," question 2 flips to *yes* and question 3 becomes live — and that is the point at which the answer changes, not before.

---

## 2. The decision matrix separates orthogonal choices

The **default rows** cover most services; specialized rows handle workflow-heavy or event-heavy systems.

| Situation | State owner | Delivery | Execution | Recommended pattern | Default? |
|---|---|---|---|---|:---:|
| Non-critical post-response action | None | Process memory | Coroutine/thread | In-process background hook | ✓ |
| Simple CPU-heavy task | Job record | Queue/job table | Process workers | Durable queue + process pool | ✓ |
| Simple blocking-I/O task | Job record | Queue/job table | Thread workers | Durable queue + bounded threads | ✓ |
| High-concurrency async I/O | Job record | Queue/job table | Async workers | Durable transport + semaphore | ✓ |
| Low/medium-volume stateful workflow | Domain database | DB job table | Specialized workers | Transactional state + jobs + leases | ✓ |
| Existing broker, fixed simple stages | Domain database | Broker + outbox | Framework workers | DB state + outbox + task workers | ✓ |
| Cloud-native independent tasks | Domain database/job store | Managed queue | Custom workers/serverless | Visibility timeout + idempotent consumers | ✓ |
| Long-lived human workflow | Workflow engine/checkpointer | Engine activities/tasks | Activity workers | Durable engine or checkpointed graph | |
| Scheduled data pipeline/backfill | Orchestrator metadata DB | Orchestrator executor | Task-specific | Airflow-class data orchestrator | |
| Independent domain reactions | Each consumer/domain DB | Event bus/log | Consumer-specific | Event choreography | |

The same system may use several rows. Separate queues and deployments are often clearer than forcing email, CPU parsing, and provider calls through one pool.

---

## 3. A broker is optional, not a maturity badge

Use a **database job table** when:

- The database is already the workflow source of truth.
- State transition and job creation must be atomic.
- Throughput is low to moderate and polling load is measurable.
- The team is prepared to own claims, leases, retries, fairness, and cleanup.

Use a **broker and task framework** when:

- Broker/worker operations already exist.
- Routing, acknowledgements, delayed delivery, process pools, and monitoring should be ready-made.
- Eventual publication after a database commit is acceptable via an outbox.
- Tasks are independent or workflow stages are few and stable.

Use a **managed queue** when:

- Cloud-managed durability and scaling are worth provider-specific semantics.
- Custom workers or serverless consumers fit the workload.
- Visibility timeouts, DLQ/redrive, payload limits, and quotas are acceptable.

No broker is required merely because a task is long. A broker is also not sufficient merely because a workflow is complex.

---

## 4. A state machine is required by meaning, not duration

Choose a job record without a business state machine when success is one independent outcome and a retry does not advance a domain lifecycle.

Choose persistent workflow state when the system must recover an allowed next action after restart, especially with approval, branching, cancellation, compensation, synchronization, or audit history.

Choose a durable workflow engine when implementing timers, signals, replay, compensation, and workflow-version evolution would become a product-sized runtime. **AWS Step Functions Standard** fits AWS-native declarative orchestration and service integrations; **Temporal** fits code-first durable service workflows and activity workers. Choose a checkpointed graph when LLM/agent state and human interrupts are central, while retaining idempotency around external side effects.

Compare the axes before choosing either in [State-Machine Design](03_state_machine_design.md), then use [Workflow Orchestrator Selection](frameworks/00_workflow_orchestrator_selection.md) to compare custom coordination, Step Functions, Temporal, Airflow, and LangGraph. The [database-backed](state_machines/02_database_backed_state_machine.md) and [event-sourced](state_machines/03_event_sourced_state_machine.md) deep dives cover application-owned alternatives.

Do not let `AsyncResult`, queue visibility, or scheduler job state become authoritative business state. Their retention and failure semantics serve execution, not the domain.

---

## 5. Concurrency follows workload and quota

| Workload | Start with | Scale until | Main failure to prevent |
|---|---|---|---|
| Pure Python CPU | Processes near CPU allocation | CPU saturated or memory limit approached | Oversubscription and OOM |
| Native code that releases GIL | Benchmark threads vs processes | Native threads/CPU saturated | Hidden internal thread multiplication |
| Blocking HTTP/DB | Bounded threads | Connection/provider quota reached | Hung threads and pool exhaustion |
| Fully async I/O | Coroutines + semaphore | Connection/provider/global limit reached | Unbounded fan-out/event-loop blocking |
| GPU/large-memory task | Specialized low-concurrency worker | Device memory/throughput optimum | Co-location OOM |

Measure queue age, throughput, failure rate, memory high-water mark, pool wait, provider latency, and event-loop lag. Worker count alone is not a success metric.

Turn those measurements into a bounded fleet plan in [Capacity Planning and Autoscaling](operations/03_capacity_planning_and_autoscaling.md). A replica target is incomplete until maximum replicas multiplied by per-pod pools and concurrency still fits database, provider, memory, and cost ceilings.

---

## 6. Frameworks cover different responsibility sets

Start with **APScheduler**, **Celery/Dramatiq**, **database or managed-queue workers**, and **durable engines**. The remaining rows are narrower execution or data-orchestration cases.

| Tool/category | Scheduler | Queue/broker abstraction | Worker runtime | Persistent workflow engine | Best fit |
|---|:---:|:---:|:---:|:---:|---|
| In-process background hook | | | In web process | | Best-effort tiny work |
| APScheduler 3.x | ✓ | | Local executors | | Timing in a controlled scheduler process |
| Celery | Beat | ✓ | ✓ | No | Mature Python task infrastructure and ecosystem |
| Dramatiq | Delayed messages, external periodic add-ons | ✓ | ✓ | No | Focused Python task processing |
| Managed queue + poller | External/platform | Provider queue | Custom | No | Cloud-managed delivery with tailored workers |
| DB polling workers | External/custom | Database | Custom | No | Transactional job creation at moderate volume |
| Airflow | ✓ | Executor-dependent | Platform executors | Data-workflow orchestration | Scheduled data pipelines and backfills |
| Temporal / Step Functions Standard | ✓/timers | Engine-managed | Activities or service integrations | ✓ | Long-lived stateful service workflows |
| LangGraph | Interrupt/timer integrations | Runtime-dependent | Graph runtime | Checkpointed graph | Agent/LLM workflows with persisted graph state |

The important entry points are **APScheduler for timing**, **Celery or Dramatiq for Python task workers**, **Airflow for data pipelines**, **Step Functions Standard or Temporal for long-lived service workflows**, and **LangGraph for checkpointed agent state**.

See [Framework Notes](frameworks/README.md) for implementation details.

---

## 7. Review the operational cost before committing

Score each candidate on the questions below. **The first five change the decision most often** — they are the ones whose answers rule a candidate out rather than merely costing it points. Treat the rest as due diligence on the candidate you have already chosen.

- **Where is authoritative business state, and can it conflict with runtime state?**
- **How are database/broker dual writes closed?**
- **What happens after a worker dies during an external side effect?**
- **Which identities may create, approve, cancel, or redrive work for each tenant?**
- **What is the first capacity ceiling, and what maximum fleet fits beneath it?**
- Can the team inspect pending, running, retrying, and dead-lettered work?
- Can an operator safely retry or cancel one job?
- Are leases/visibility extended for long jobs?
- Are per-worker, per-pod, per-tenant, and global limits distinct?
- Can the platform meet payload, retention, ordering, and audit requirements?
- What data and messages must be deleted for privacy or cost control?
- Which component is on call, and what is the manual recovery procedure?

Use [Security and Authorization](operations/01_security_and_authorization.md) to review trigger and operator boundaries. Use [Multitenancy, Admission, and Fairness](operations/02_multitenancy_admission_and_fairness.md) to distinguish request rate, durable backlog, in-flight work, and cost budgets.

⚠️ Avoid a choice whose happy path is easy but whose redrive procedure cannot explain why repeating a side effect is safe.

⚠️ Avoid running two schedulers, queues, or state machines for the same responsibility unless the ownership boundary is documented and tested.

---

## 8. Validate the choice with failure tests

Before production, implement the complete [failure-injection matrix](08_failure_injection_and_testing.md) and demonstrate:

The first seven checks are the **default recovery contract**. The final two become required when external principals or multiple tenants share the system.

1. Kill a worker after the external side effect but before completion; redelivery does not duplicate the effect.
2. Run two workers against one job; only one holds valid ownership.
3. Race cancellation against completion; one versioned transition wins.
4. Stop publication after the database commit; outbox/reconciliation eventually creates delivery.
5. Exhaust retries; work becomes visible in the failed/DLQ path with a safe redrive procedure.
6. Scale pods to the maximum; global provider and database limits remain bounded.
7. Restart the scheduler or workflow runtime; durable schedules/checkpoints resume according to policy.
8. Submit unauthorized and cross-tenant commands; denial creates no durable work.
9. Flood one tenant; other tenants continue within their queue-age SLO while all budgets stay bounded.

The design works when these tests leave an explainable state and an operator can recover without editing rows blindly.

---

**Next**: [Framework Notes](frameworks/README.md)
