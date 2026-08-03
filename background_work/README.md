# Background Work

> A failure-first design course for work that outlives a request, process, delivery, or human wait.

[![Python](https://img.shields.io/badge/Python-background_workers-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-durable_state-4169E1.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Temporal](https://img.shields.io/badge/Temporal-durable_workflows-141414.svg)](https://temporal.io/)
[![LangGraph](https://img.shields.io/badge/LangGraph-checkpointed_graphs-1C3C3C.svg)](https://www.langchain.com/langgraph)

The framework-neutral notes build one editorial workflow from failure to mechanism to implementation to verification. Framework notes show which responsibilities particular runtimes own; they are not substitutes for the business guarantees in the reliability section.

---

## Core Design

| File | Topic | Description |
|---|---|---|
| [01_overview.md](01_overview.md) | Responsibility model | Separates workflow, execution, delivery, workers, schedulers, and engines |
| [02_when_a_task_becomes_a_workflow.md](02_when_a_task_becomes_a_workflow.md) | Workflow threshold | Decides when progress has durable business meaning and when one job is enough |
| [03_state_machine_design.md](03_state_machine_design.md) | Three design axes | Separates transition modeling, state persistence/concurrency, and work execution |
| [state_machines/](state_machines/README.md) | State-machine deep dives | Compares code models, relational CAS, event streams, and the complete workflow lifecycle |

---

## Delivery, Execution, and Timing

| File | Topic | Description |
|---|---|---|
| [04_queue_and_worker_architectures.md](04_queue_and_worker_architectures.md) | Queue architectures | Compares database queues, brokers/outbox, managed queues, engines, and choreography |
| [05_task_execution_models.md](05_task_execution_models.md) | Execution models | Selects processes, bounded threads, or bounded coroutines from workload behavior |
| [06_scheduling_and_periodic_work.md](06_scheduling_and_periodic_work.md) | Scheduling | Defines cron/interval, timezone/DST, misfire, catch-up, overlap, and replica-safe firings |

---

## Reliability and Composition

| File | Topic | Description |
|---|---|---|
| [reliability/](reliability/README.md) | Reliability deep dives | Implements atomic intent, fencing, idempotency, retry/cancellation, reconciliation, and operations |
| [07_durable_fanout_and_join.md](07_durable_fanout_and_join.md) | Fan-out/join | Persists bounded child sets, idempotent completion, and one aggregate handoff |

---

## Production Operations

| File | Topic | Description |
|---|---|---|
| [operations/](operations/README.md) | Security, tenancy, and capacity | Secures trigger/control paths, bounds tenant demand, preserves fairness, and sizes worker fleets against shared ceilings |
| [08_failure_injection_and_testing.md](08_failure_injection_and_testing.md) | Failure testing | Exercises crash, redelivery, lease, authorization, tenant-isolation, cancellation, outbox, retry, and redrive races |

---

## Selection and Frameworks

| File | Topic | Description |
|---|---|---|
| [09_decision_guide.md](09_decision_guide.md) | Decision guide | Turns durability, state, workload, and operational constraints into the smallest suitable system |
| [frameworks/](frameworks/README.md) | Framework notes | Worker runtimes, schedulers, orchestrator selection, Step Functions, Temporal, Airflow, and LangGraph |

---

## Reading Order

1. **Full design course** — `01` → `02` → `03` → `state_machines/` → `04` → `05` → `06` → `reliability/` → `07` → `operations/` → `08` → `09`.
2. **Implement the database-backed default** — read `01`–`03`, then [the end-to-end workflow](state_machines/04_end_to_end_workflow.md), all [reliability deep dives](reliability/README.md), and the [production operations](operations/README.md) before running the failure matrix.
3. **Choose quickly** — start with the [Decision Guide](09_decision_guide.md), then follow its links to the mechanism that changes the decision.
4. **Evaluate orchestration runtimes** — read [State-Machine Design](03_state_machine_design.md), then [Workflow Orchestrator Selection](frameworks/00_workflow_orchestrator_selection.md), then the relevant [framework note](frameworks/README.md).

---

## Related Sections

- [Long-running task patterns](../architecture/long_running_tasks/README.md) — client delivery, callbacks, task tokens, and infrastructure-specific examples
- [Concurrency fundamentals](../fundamentals/concurrency/README.md) — event loops, threads, processes, and synchronization
- [API idempotency](../fundamentals/fastapi/safe_and_scalable_api_calls/11_idempotency.md) — stable request keys and replay-safe API semantics
- [Distributed admission control](../fundamentals/fastapi/safe_and_scalable_api_calls/09_distributed_admission_control.md) — Redis-backed request, tenant, provider, and global limit mechanics
- [Redis rate limiting](../infrastructure/redis/05_rate_limiting.md) — fixed window, sliding window, and token-bucket primitives

---

## Prerequisites

- Comfortable reading Python and PostgreSQL
- Basic familiarity with transactions, HTTP clients, and containerized services
- No prior knowledge of a task-queue or workflow framework is assumed
