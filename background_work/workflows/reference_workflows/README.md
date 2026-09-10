# Reference Workflows

> Capstone walkthroughs that assemble state, delivery, execution, recovery, and verification without taking ownership away from their focused deep dives.

---

## Contents

| File | Role | Topic | Reader outcome |
|---|---|---|---|
| [01_database_backed_editorial_workflow.md](01_database_backed_editorial_workflow.md) | Reference implementation | Database-backed editorial workflow | Trace one API command through atomic intent, dispatch, claim, provider effect, completion, retry, cancellation, and reconciliation |

---

## Reading Order

**Working result by entry 1**: follow the durable evidence chain for one successful workflow and replay the same lifecycle through its crash windows.

1. **Assemble:** [Database-Backed Editorial Workflow](01_database_backed_editorial_workflow.md) — connect the mechanisms already introduced by state-machine persistence and reliability.

**Stop here if** the reference workflow matches the product's recovery contract. Continue to [Failure Injection and Testing](../../reliability/06_failure_injection_and_testing.md) when implementing the crash-window acceptance suite.

---

## Prerequisites

- [Relational Current-State Persistence](../state_machines/persistence/01_relational_current_state.md)
- [Background-Work Reliability](../../reliability/README.md)
