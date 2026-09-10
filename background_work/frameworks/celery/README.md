# Celery

> Celery’s task-delivery, worker-pool, scheduling, and business-state boundaries.

[![Celery](https://img.shields.io/badge/Celery-task_queue-37814A.svg)](https://docs.celeryq.dev/)

---

## Contents

| File | Topic | Description |
|---|---|---|
| [overview.md](overview.md) | Celery | Brokers, results, pools, acknowledgement, retry, routing, Beat, outbox, and production fit |

---

## Reading Order

**Working result by entry 1**: run the overview's smallest broker-backed task and observe the worker result.

1. **Do:** [Celery overview](overview.md) — run one task before evaluating the full runtime.
2. **Understand when needed:** [Queue and worker architectures](../../execution/01_queue_and_worker_architectures.md) and [task execution models](../../execution/02_task_execution_models.md).
3. **Decide:** use the [general decision guide](../00_system_selection.md) before committing to Celery for durable business state or multi-step workflows.

**Stop here if** Celery's delivery, pool, and acknowledgement boundaries match one independent
task. Continue into reliability notes when effects, replay, cancellation, or repair become product contracts.

---

## Prerequisites

- [Queue and worker architectures](../../execution/01_queue_and_worker_architectures.md)
- [Task execution models](../../execution/02_task_execution_models.md)
