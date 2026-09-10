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
| [06_failure_injection_and_testing.md](06_failure_injection_and_testing.md) | Failure verification | Proves crash, redelivery, fencing, cancellation, retry, authorization, and redrive invariants |

---

## Which Mechanism Protects Which Hop

```text
Client → API → DB
    └── Idempotency-Key header
        protects against the client retrying and creating a second run

DB → Queue/Broker
    └── Transactional outbox (01)
        protects against a crash between commit and publish

Queue → Worker → DB
    ├── Message/event ID
    │   protects against duplicate delivery
    └── Expected version / attempt token (02)
        protects against stale or concurrent writers

Worker → External provider
    └── Stable operation key (03)
        protects the business effect across retries and lease recovery
```

Each hop fails independently, so each gets its own key — do not let one identifier do two jobs:

| Identifier | Answers | Lifetime |
|---|---|---|
| `job.id` / `run_id` | Which job or run is this? | The whole record's life |
| `idempotency_key` / operation key | Which business operation is this? | The whole logical operation — survives every retry |
| `attempt_token` (fencing) | Who owns this attempt right now? | One ownership epoch — replaced on every claim |
| `worker_id` | Which executor is running it? | Logging/debugging only — never a fencing predicate |

---

## Jobs Table: Field Reference

No single file shows one complete `CREATE TABLE jobs` with every column, on purpose — each file teaches the mechanism it owns, and a job that is never a workflow step doesn't need workflow columns. This is every field that appears across the notes, by concern, so you can see the full set in one place and pick only what your job actually needs:

| Concern | Answers | Column(s) seen in the notes | Introduced in |
|---|---|---|---|
| Identity | Which job is this? | `id` | [Minimal Durable Task](../foundations/03_minimal_durable_task.md), [01](01_atomic_transitions_and_outbox.md) |
| Parent link | Which workflow/step does this belong to (NULL if standalone)? | `workflow_run_id`, `step` | [01](01_atomic_transitions_and_outbox.md), [Scheduling](../execution/03_scheduling_and_periodic_work.md) |
| Tenant scope | Which tenant owns this job? | `tenant_id` | [Minimal Durable Task](../foundations/03_minimal_durable_task.md) |
| Input | Where does the input live? (keep large payloads out of the hot polling row) | `payload` (small) / `input_ref` (large, external) | [01](01_atomic_transitions_and_outbox.md), [Minimal Durable Task](../foundations/03_minimal_durable_task.md) |
| Business dedup | Which logical operation is this — stable across every retry? | `idempotency_key`, or caller-supplied `request_key` + `request_hash` | [01](01_atomic_transitions_and_outbox.md), [Minimal Durable Task](../foundations/03_minimal_durable_task.md), [Scheduling](../execution/03_scheduling_and_periodic_work.md), [03](03_idempotency_and_external_effects.md) |
| Result | Where does the terminal output live? | `result_ref` | [Minimal Durable Task](../foundations/03_minimal_durable_task.md), [02](02_leases_heartbeats_and_fencing.md) |
| Lifecycle | What state is it in? | `status` | [02](02_leases_heartbeats_and_fencing.md), [Minimal Durable Task](../foundations/03_minimal_durable_task.md) |
| Retry scheduling | How many attempts, how many allowed, when is the next claimable? | `attempt`, `max_attempts`, `next_attempt_at` | [Minimal Durable Task](../foundations/03_minimal_durable_task.md) |
| Claim order | In what order are ready rows claimed? | `priority` | [02](02_leases_heartbeats_and_fencing.md) |
| Ownership epoch (fencing token + lease) | Who owns this attempt right now, and until when? | `attempt_token`, `lease_expires_at` | [02](02_leases_heartbeats_and_fencing.md), [Minimal Durable Task](../foundations/03_minimal_durable_task.md) |
| Executor identity | Which process/pod is running it? (logging only — never a fencing predicate) | `worker_id` | [02](02_leases_heartbeats_and_fencing.md) |
| Failure evidence | Why did the last attempt end? | `last_error_class`, `last_error` | [Minimal Durable Task](../foundations/03_minimal_durable_task.md), [02](02_leases_heartbeats_and_fencing.md) |
| Audit trail | When was it created, started, finished? | `created_at`, `started_at`, `finished_at` | [Minimal Durable Task](../foundations/03_minimal_durable_task.md), [02](02_leases_heartbeats_and_fencing.md) |

[03_minimal_durable_task.md](../foundations/03_minimal_durable_task.md)'s `document_parse_jobs` is the closest complete, runnable example. Add `workflow_run_id`/`step` only once the job is actually a workflow step (01); add `priority` only once claim order needs to beat FIFO (02).

`SKIP LOCKED` is not a column and won't appear in a fields table — it's the row-locking clause the claim query uses so concurrent workers skip rows another transaction already has locked instead of waiting on them. See [02 §2](02_leases_heartbeats_and_fencing.md) for the claim SQL and [Queue Architectures §2](../execution/01_queue_and_worker_architectures.md) for the claim-loop/recovery-loop architecture around it.

---

## Reading Order

1. **Atomic intent** — ensure the system never owes work without recording it.
2. **Leases and fencing** — make worker ownership recoverable after process loss.
3. **Idempotency** — protect effects that the database cannot roll back.
4. **Retries and cancellation** — make control decisions durable and race-safe.
5. **Reconciliation and operations** — find failures no request or worker observed.
6. **Failure injection** — interrupt the system between durable facts and verify that every invariant survives.

**Next**: assemble the full evidence chain in the [Database-Backed Editorial Workflow](../workflows/reference_workflows/01_database_backed_editorial_workflow.md), then run the [Failure Injection Matrix](06_failure_injection_and_testing.md).

---

## Prerequisites

- [Relational Current-State Persistence](../workflows/state_machines/persistence/01_relational_current_state.md)
- [Queue and Worker Architectures](../execution/01_queue_and_worker_architectures.md)
