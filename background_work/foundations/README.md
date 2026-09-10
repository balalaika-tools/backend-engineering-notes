# Background-Work Foundations

> Separate business state, delivery, and execution, then build the smallest durable task before adding workflow machinery.

---

## Contents

| File | Role | Topic | Reader outcome |
|---|---|---|---|
| [01_overview.md](01_overview.md) | Foundation | Responsibility model | Explain which system owns business progress, task execution, delivery, scheduling, and coordination |
| [02_task_or_workflow.md](02_task_or_workflow.md) | Decision guide | Task-to-workflow threshold | Decide whether one durable job is enough or intermediate progress needs business state |
| [03_minimal_durable_task.md](03_minimal_durable_task.md) | Implementation | Minimal durable task | Submit, claim, retry, and inspect one replay-safe database-backed job |

---

## Reading Order

**Working result by entry 2**: classify a workload as one durable task or a stateful workflow and identify which state belongs to the application.

1. **Understand:** [Responsibility Model](01_overview.md) — separate state ownership from delivery and compute.
2. **Decide:** [Task or Workflow?](02_task_or_workflow.md) — identify whether intermediate progress has durable business meaning.
3. **Do:** [Minimal Durable Task](03_minimal_durable_task.md) — build the smallest recoverable job and status endpoint.

**Stop here if** one independent, replay-safe task and a result lookup endpoint meet the product need. Continue to [Execution](../execution/README.md) to choose delivery and compute, or [Workflows](../workflows/README.md) when branching, durable waits, joins, or compensation become business requirements.

---

## Prerequisites

- Comfortable reading Python, SQL, and HTTP APIs
- Basic familiarity with database transactions

