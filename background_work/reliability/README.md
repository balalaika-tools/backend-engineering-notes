# Background-Work Reliability

> Failure-first implementation notes for turning durable intent into replay-safe execution and explainable recovery.

---

## Contents

| File | Topic | Description |
|---|---|---|
| [01_atomic_transitions_and_outbox.md](01_atomic_transitions_and_outbox.md) | Atomic intent | Commits state, job, history, and outbox together, then publishes with confirmation |
| [02_leases_heartbeats_and_fencing.md](02_leases_heartbeats_and_fencing.md) | Temporary ownership | Recovers crashed attempts and rejects late writes with a unique attempt token |
| [03_idempotency_and_external_effects.md](03_idempotency_and_external_effects.md) | Replay-safe effects | Validates request hashes, reuses stable provider keys, and recovers ambiguous outcomes |
| [04_retries_timeouts_and_cancellation.md](04_retries_timeouts_and_cancellation.md) | Durable control | Persists retry schedules and budgets, bounds calls, races cancellation safely, and compensates |
| [05_reconciliation_dlq_and_observability.md](05_reconciliation_dlq_and_observability.md) | Operational recovery | Repairs silent gaps, quarantines exhausted work, correlates evidence, and drains workers safely |

---

## Reading Order

1. **Atomic intent** — ensure the system never owes work without recording it.
2. **Leases and fencing** — make worker ownership recoverable after process loss.
3. **Idempotency** — protect effects that the database cannot roll back.
4. **Retries and cancellation** — make control decisions durable and race-safe.
5. **Reconciliation and operations** — find failures no request or worker observed.

---

## Prerequisites

- [End-to-End Database-Backed Workflow](../state_machines/04_end_to_end_workflow.md)
- [Queue and Worker Architectures](../04_queue_and_worker_architectures.md)
