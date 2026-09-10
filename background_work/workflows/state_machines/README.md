# State-Machine Modeling and Persistence

> Model legal workflow transitions first, then choose whether a relational row or an event stream owns durable state.

---

## Contents

| File | Role | Topic | Reader outcome |
|---|---|---|---|
| [01_transition_modeling.md](01_transition_modeling.md) | Decision guide | Transition modeling | Choose between `match`, a registry, state objects, and hierarchical statecharts without coupling decisions to execution |
| [persistence/](persistence/README.md) | Implementation branch | Persistence models | Implement the relational default or evaluate event sourcing as an alternative authority model |

---

## Reading Order

**Working result by entry 2**: derive a legal transition from a named event, then commit its state, history, and execution intent atomically in PostgreSQL.

1. **Decide:** [Transition Modeling](01_transition_modeling.md) — keep guards, target-state derivation, and durable commands behind one inspectable owner.
2. **Build the default:** [Relational Current-State Persistence](persistence/01_relational_current_state.md) — make the current-state row authoritative and close the state/job write race.
3. **Choose only when history must own state:** [Event-Sourced State](persistence/02_event_sourced_state.md) — replace the mutable authority with an ordered, replayable event stream.

**Stop here if** a relational current-state row plus append-only transition history meets the recovery and audit requirements. Continue to event sourcing only when replayable history must be authoritative, or to [Background-Work Reliability](../../reliability/README.md) when workers must survive crashes and ambiguous external effects.

---

## Prerequisites

- [When a Task Becomes a Workflow](../../foundations/02_task_or_workflow.md)
- [State-Machine Design Has Three Independent Axes](../01_state_machine_design.md)
