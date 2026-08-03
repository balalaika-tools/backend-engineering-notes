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

Start with the **workflow orchestrator selection** row only when a process has durable multi-step coordination; otherwise jump directly to the scheduler or worker runtime that matches the task.

| Framework | Role | Notes |
|---|---|---|
| **[Workflow orchestrator selection](00_workflow_orchestrator_selection.md)** | **Architecture decision** | **Relates custom DB/broker coordination to Step Functions, Temporal, Airflow, and LangGraph** |
| [Celery](celery/README.md) | Task queue and worker runtime | Brokers, results, pools, acknowledgements, routing, retries, Beat, and outbox boundaries |
| [Dramatiq](dramatiq/README.md) | Task queue and worker runtime | Actors, brokers, middleware, retries, rate limits, composition, and monitoring |
| [Dramatiq + FastAPI](dramatiq/fastapi_integration.md) | Web integration | Durable status records, broker initialization, testing, containers, and worker scaling |
| [APScheduler](apscheduler/README.md) | Scheduler | Triggers, FastAPI integration, misfires, persistent stores, overlap, and multi-instance deployment |
| [Airflow](airflow/README.md) | Data-workflow orchestrator | DAGs, scheduling, backfills, executors, metadata, and operational boundaries |
| [Temporal-class engines](temporal/README.md) | Durable workflow engine | Deterministic workflow replay, activities, timers, signals, versioning, and idempotency boundaries |
| [LangGraph](langgraph/README.md) | Checkpointed graph runtime | Persisted graph state, interrupts/resume, time travel, graph evolution, and replay-safe effects |

---

## Reading Order

1. **[Decision Guide](../09_decision_guide.md)** — choose the architectural role before a framework.
2. **[Workflow orchestrator selection](00_workflow_orchestrator_selection.md)** — if the process has durable multi-step coordination, compare the engine boundary before selecting a product.
3. **Relevant framework overview** — learn its runtime and failure semantics.
4. **[Reliability Deep Dives](../reliability/README.md)** — add business guarantees that no framework supplies automatically.

---

## Prerequisites

- [Background Work overview](../01_overview.md)
- [Queue and worker architectures](../04_queue_and_worker_architectures.md)
- [Task execution models](../05_task_execution_models.md)
