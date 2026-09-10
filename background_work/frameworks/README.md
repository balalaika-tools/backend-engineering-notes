# Background Work Frameworks

> Framework-specific application of the general scheduling, delivery, execution, and workflow principles.

[![Celery](https://img.shields.io/badge/Celery-task_queue-37814A.svg)](https://docs.celeryq.dev/)
[![Dramatiq](https://img.shields.io/badge/Dramatiq-task_queue-7B4EA6.svg)](https://dramatiq.io/)
[![APScheduler](https://img.shields.io/badge/APScheduler-scheduler-4B8BBE.svg)](https://apscheduler.readthedocs.io/)
[![Airflow](https://img.shields.io/badge/Airflow-orchestrator-017CEE.svg?logo=apacheairflow&logoColor=white)](https://airflow.apache.org/)
[![AWS Step Functions](https://img.shields.io/badge/AWS_Step_Functions-managed_orchestration-FF4F8B.svg?logo=amazonaws&logoColor=white)](https://docs.aws.amazon.com/step-functions/)
[![Temporal](https://img.shields.io/badge/Temporal-durable_workflows-141414.svg)](https://temporal.io/)
[![LangGraph](https://img.shields.io/badge/LangGraph-checkpointed_graphs-1C3C3C.svg)](https://www.langchain.com/langgraph)

---

## Contents

Start with **system selection** when the delivery or runtime shape is still open. Use **workflow orchestrator selection** only when the process has durable multi-step coordination.

| Framework | Role | Notes |
|---|---|---|
| **[System selection](00_system_selection.md)** | **System decision** | **Chooses the smallest state owner, delivery mechanism, execution model, and runtime from the recovery contract** |
| **[Workflow orchestrator selection](01_workflow_orchestrator_selection.md)** | **Architecture decision** | **Relates custom DB/broker coordination to Step Functions, Temporal, Airflow, and LangGraph** |
| [Celery](celery/README.md) | Task queue and worker runtime | Brokers, results, pools, acknowledgements, routing, retries, Beat, and outbox boundaries |
| [Dramatiq](dramatiq/README.md) | Task queue and worker runtime | Actors, brokers, middleware, retries, rate limits, composition, and monitoring |
| [Dramatiq + FastAPI](dramatiq/fastapi_integration.md) | Web integration | Durable status records, broker initialization, testing, containers, and worker scaling |
| [APScheduler](apscheduler/README.md) | Scheduler | Triggers, FastAPI integration, misfires, persistent stores, overlap, and multi-instance deployment |
| [Airflow](airflow/README.md) | Data-workflow orchestrator | DAGs, scheduling, backfills, executors, metadata, and operational boundaries |
| [Temporal-class engines](temporal/README.md) | Durable workflow engine | Deterministic workflow replay, activities, timers, signals, versioning, and idempotency boundaries |
| [LangGraph](langgraph/README.md) | Checkpointed graph runtime | Persisted graph state, interrupts/resume, time travel, graph evolution, and replay-safe effects |

---

## Reading Order

Choose one deterministic branch; do not read every framework.

### Run a scheduled callback

**Working result by entry 1**: execute one persisted, overlap-aware APScheduler firing.

1. **Do:** [APScheduler overview](apscheduler/overview.md).
2. **Understand:** [Scheduling and periodic work](../execution/03_scheduling_and_periodic_work.md) — distinguish a stored schedule from an exactly-once business effect.

**Stop here if** one application owns scheduling and execution. Continue to a task queue when work
must be distributed independently of the scheduler process.

### Run independent broker-backed tasks

**Working result by entry 1**: execute one worker task and observe its result.

1. **Do:** choose [Celery](celery/overview.md) or [Dramatiq](dramatiq/overview.md), not both.
2. **Understand:** [Queue and worker architectures](../execution/01_queue_and_worker_architectures.md) and [task execution models](../execution/02_task_execution_models.md).
3. **Harden:** [Atomic transitions and outbox](../reliability/01_atomic_transitions_and_outbox.md), [idempotent effects](../reliability/03_idempotency_and_external_effects.md), and [reconciliation](../reliability/05_reconciliation_dlq_and_observability.md).

**Stop here if** each job is an independent unit with application-owned status. Continue to an
engine when the product needs durable multi-step coordination, signals, or long-lived timers.

### Run a durable workflow

**Working result by entry 2**: choose an engine boundary and run the selected framework's smallest
durable workflow or checkpoint/resume trace.

1. **Decide:** [Workflow orchestrator selection](01_workflow_orchestrator_selection.md).
2. **Do one branch:** [Temporal](temporal/overview.md), [Airflow](airflow/overview.md), or [LangGraph](langgraph/overview.md), according to that decision.
3. **Harden:** [Retries, timeouts, and cancellation](../reliability/04_retries_timeouts_and_cancellation.md) plus the exact reliability note named by the chosen engine's external-effect boundary.

**Stop here if** the evaluation proves the required recovery, deployment, and external-effect
contract. Compare a second product only when a named requirement remains unmet.

---

## Prerequisites

- [Background-Work Foundations](../foundations/README.md)
- [Queue and worker architectures](../execution/01_queue_and_worker_architectures.md)
- [Task execution models](../execution/02_task_execution_models.md)
