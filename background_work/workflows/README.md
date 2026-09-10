# Stateful Workflows

> Model durable business progress, implement its state authority, and compose multiple tasks without confusing workflow state with worker state.

---

## Contents

| File | Role | Topic | Reader outcome |
|---|---|---|---|
| [01_state_machine_design.md](01_state_machine_design.md) | Decision guide | State-machine axes | Choose transition modeling, persistence, and execution independently |
| [state_machines/](state_machines/README.md) | Implementation branch | Modeling and persistence | Implement named-event decisions with relational or event-sourced state authority |
| [02_durable_fanout_and_join.md](02_durable_fanout_and_join.md) | Deep dive | Fan-out and join | Persist bounded child sets and perform exactly one aggregate handoff |
| [reference_workflows/](reference_workflows/README.md) | Reference implementation | Complete workflow | Trace API command, dispatch, execution, external effects, recovery, and verification together |

---

## Reading Order

**Working result by entry 2**: derive one legal named transition and commit its new state, evidence, and execution intent atomically.

1. **Decide:** [State-Machine Design](01_state_machine_design.md) — separate the three independent design axes.
2. **Build:** [State-Machine Modeling and Persistence](state_machines/README.md) — use relational current state as the default authority model.
3. **Compose only when needed:** [Durable Fan-Out and Join](02_durable_fanout_and_join.md).
4. **Assemble after hardening:** follow [Reliability](../reliability/README.md), then the [Database-Backed Reference Workflow](reference_workflows/README.md).

**Stop here if** one relational state machine and sequential durable jobs express the lifecycle. Continue to fan-out only when a bounded child set must execute concurrently, or to a workflow engine when timers, signals, parallel regions, and migrations become a runtime of their own.

---

## Prerequisites

- [Task or Workflow?](../foundations/02_task_or_workflow.md)
- [Minimal Durable Task](../foundations/03_minimal_durable_task.md)

