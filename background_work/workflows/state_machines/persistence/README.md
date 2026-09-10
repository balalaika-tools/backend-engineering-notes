# State-Machine Persistence Models

> Choose which durable record is authoritative and how concurrent commands are serialized.

---

## Contents

| File | Role | Topic | Reader outcome |
|---|---|---|---|
| [01_relational_current_state.md](01_relational_current_state.md) | Implementation | Relational current state | Commit a derived transition, audit evidence, and owed work in one conditional transaction |
| [02_event_sourced_state.md](02_event_sourced_state.md) | Deep dive | Event-sourced state | Evaluate and implement an ordered event stream as the workflow's source of truth |

---

## Reading Order

**Working result by entry 1**: persist one named transition with optimistic concurrency and no state/job dual-write gap.

1. **Build the default:** [Relational Current-State Persistence](01_relational_current_state.md).
2. **Compare the alternative:** [Event-Sourced State](02_event_sourced_state.md) only when immutable history, replay, or multiple projections must define current state.

**Stop here if** the relational model provides sufficient recovery, auditability, and concurrency control. Event sourcing adds operational and schema-evolution obligations that an audit log does not require.

---

## Prerequisites

- [Transition Modeling](../01_transition_modeling.md)
- [State-Machine Design Has Three Independent Axes](../../01_state_machine_design.md)
