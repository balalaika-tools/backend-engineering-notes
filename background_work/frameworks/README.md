# Background Work Frameworks

> Framework-specific application of the general scheduling, delivery, execution, and workflow principles.

[![Celery](https://img.shields.io/badge/Celery-task_queue-37814A.svg)](https://docs.celeryq.dev/)
[![Dramatiq](https://img.shields.io/badge/Dramatiq-task_queue-7B4EA6.svg)](https://dramatiq.io/)
[![APScheduler](https://img.shields.io/badge/APScheduler-scheduler-4B8BBE.svg)](https://apscheduler.readthedocs.io/)
[![Airflow](https://img.shields.io/badge/Airflow-orchestrator-017CEE.svg?logo=apacheairflow&logoColor=white)](https://airflow.apache.org/)
[![Temporal](https://img.shields.io/badge/Temporal-durable_workflows-141414.svg)](https://temporal.io/)
[![LangGraph](https://img.shields.io/badge/LangGraph-checkpointed_graphs-1C3C3C.svg)](https://www.langchain.com/langgraph)

---

## Contents

| Framework | Role | Notes |
|---|---|---|
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
2. **Relevant framework overview** — learn its runtime and failure semantics.
3. **[Reliability Deep Dives](../reliability/README.md)** — add business guarantees that no framework supplies automatically.

---

## Prerequisites

- [Background Work overview](../01_overview.md)
- [Queue and worker architectures](../04_queue_and_worker_architectures.md)
- [Task execution models](../05_task_execution_models.md)
