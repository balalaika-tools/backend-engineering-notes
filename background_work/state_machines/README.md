# State-Machine Implementations

> The same editorial workflow implemented through different transition and persistence models, then assembled into one recoverable worker lifecycle.

---

## Contents

| File | Topic | Description |
|---|---|---|
| [01_application_code_approaches.md](01_application_code_approaches.md) | Transition modeling | Compares `match`, registries, state objects, and hierarchical statecharts with the same named event |
| [02_database_backed_state_machine.md](02_database_backed_state_machine.md) | Relational persistence | Enforces named transitions with optimistic CAS, atomic job creation, history, versioning, and cancellation |
| [03_event_sourced_state_machine.md](03_event_sourced_state_machine.md) | Event sourcing | Uses an authoritative event stream, expected stream versions, projections, commands, and schema evolution |
| [04_end_to_end_workflow.md](04_end_to_end_workflow.md) | Complete workflow | Connects API command, atomic transition, job claim, fencing, idempotency, retry, and reconciliation |

---

## Reading Order

1. **Application-code approaches** — learn where legal transitions live before choosing storage.
2. **Database-backed state** — implement the repository default for service workflows.
3. **Event-sourced state** — understand when history, rather than a current-state row, is authoritative.
4. **End-to-end workflow** — see every responsibility cooperate across one failure-injected run.

---

## Prerequisites

- [When a Task Becomes a Workflow](../02_when_a_task_becomes_a_workflow.md)
- [State-Machine Design Has Three Independent Axes](../03_state_machine_design.md)
