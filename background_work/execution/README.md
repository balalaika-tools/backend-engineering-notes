# Background-Work Execution

> Choose how pending work is delivered, which concurrency model executes it, and how schedules create durable work.

---

## Contents

| File | Role | Topic | Reader outcome |
|---|---|---|---|
| [01_queue_and_worker_architectures.md](01_queue_and_worker_architectures.md) | Decision guide | Delivery architecture | Choose between database polling, brokers, managed queues, and workflow engines from failure semantics |
| [02_task_execution_models.md](02_task_execution_models.md) | Decision guide | Worker concurrency | Match processes, bounded threads, or bounded coroutines to the workload bottleneck |
| [03_scheduling_and_periodic_work.md](03_scheduling_and_periodic_work.md) | Implementation | Durable scheduling | Persist one firing across replicas while handling timezones, misfires, overlap, and catch-up |

---

## Reading Order

**Working result by entry 2**: choose both the durable home of pending work and the bounded compute model that executes it.

1. **Choose delivery:** [Queue and Worker Architectures](01_queue_and_worker_architectures.md).
2. **Choose compute:** [Task Execution Models](02_task_execution_models.md).
3. **Add time when needed:** [Scheduling and Periodic Work](03_scheduling_and_periodic_work.md).

**Stop here if** work is delivered durably, execution is bounded, and no calendar or interval trigger is required. Continue to [Reliability](../reliability/README.md) when crashes, duplicate delivery, external effects, or cancellation must be recovered safely.

---

## Prerequisites

- [Background-Work Foundations](../foundations/README.md)
- [Concurrency Fundamentals](../../fundamentals/concurrency/README.md)

