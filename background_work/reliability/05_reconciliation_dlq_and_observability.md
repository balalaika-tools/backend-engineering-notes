# Reconciliation Finds Failures No Handler Saw

> **Who this is for**: Engineers operating durable jobs after normal success, exception, and retry paths are already implemented.

Before reading this, understand the durable terminal transition in **[Retries, Timeouts, and Cancellation](04_retries_timeouts_and_cancellation.md)**.

---

## 1. A killed process leaves no exception to handle

A worker calls the provider, the host loses power, and neither `except` nor `finally` runs. The queue may redeliver, but it cannot know whether local state and provider evidence agree. Normal handlers cover observed outcomes; a **reconciler** finds silence and contradictions.

```text
expected lifecycle: PENDING → RUNNING → SUCCEEDED | PENDING(retry) | FAILED
silent evidence:    RUNNING + expired lease
contradiction:      COMPLETED + no result/provider reference
missing handoff:    *_QUEUED + no job/outbox evidence
```

The reconciler is a bounded, idempotent command handler. It never “fixes” rows by guessing; it proves a precondition, applies one conditional repair, and records the evidence.

> **The near-miss**: a daily script that sets every old `RUNNING` row to `PENDING` is cleanup, not reconciliation. It ignores attempt tokens, retry budgets, provider ambiguity, and newer owners.

---

## 2. Start with four consistency implications

The **core four** cover the failure windows introduced by state, jobs, outbox, and external effects.

| Expected implication | Contradicting evidence | Conditional repair or escalation |
|---|---|---|
| **`RUNNING` has a live owner** | Lease expired | Recover provider result or return to retry budget; fence old token |
| **`*_QUEUED` has durable work** | No compatible job/outbox row | Recreate original deterministic intent or alert |
| **Terminal success has result evidence** | `COMPLETED` without result/provider reference | Recover from provider operation; otherwise incident |
| **Every job belongs to a legal workflow step** | Orphan or incompatible state/version | Mark stale; never execute blindly |
| Published outbox eventually reaches consumer evidence | Old published row, no job/inbox progress | Inspect routing and consumer health |
| Inbox claim has a local effect | Message recorded, expected transition absent | Replay atomic consumer transaction or escalate contract defect |
| Projection matches authoritative history | Version gap or wrong state | Stop stream, replay missing events, rebuild projection |
| Pending work meets queue-age SLO | Old eligible row | Inspect capacity, indexes, fairness, and dependency health |

Every repair preserves the original operation, job, message, and command keys. A new identifier turns recovery into a second business action.

> **Key insight**: Reconciliation turns invariants into executable operations. If a design cannot state the implication it checks, it cannot distinguish repair from data corruption.

---

## 3. Expired-lease repair is bounded and token-aware

Select a limited batch under row locks, then update only the token observed by that scan:

```sql
WITH expired AS (
    SELECT id, attempt_token, attempt, first_failed_at
    FROM jobs
    WHERE status = 'RUNNING'
      AND lease_expires_at <= now()
    ORDER BY lease_expires_at
    FOR UPDATE SKIP LOCKED
    LIMIT :repair_limit
),
repaired AS (
    UPDATE jobs AS j
    SET status = CASE
            WHEN expired.attempt >= :max_attempts
              OR now() >= COALESCE(expired.first_failed_at, now()) + :max_retry_age
                THEN 'FAILED'
            ELSE 'PENDING'
        END,
        next_attempt_at = CASE
            WHEN expired.attempt >= :max_attempts
              OR now() >= COALESCE(expired.first_failed_at, now()) + :max_retry_age
                THEN NULL
            ELSE now() + (:backoff_seconds * interval '1 second')
        END,
        first_failed_at = COALESCE(expired.first_failed_at, now()),
        last_error_class = 'LEASE_EXPIRED',
        last_error = 'worker stopped heartbeating',
        terminal_reason = CASE
            WHEN expired.attempt >= :max_attempts THEN 'ATTEMPT_BUDGET_EXHAUSTED'
            WHEN now() >= COALESCE(expired.first_failed_at, now()) + :max_retry_age
                THEN 'WALL_CLOCK_BUDGET_EXHAUSTED'
            ELSE NULL
        END,
        attempt_token = NULL,
        worker_id = NULL,
        lease_expires_at = NULL,
        finished_at = CASE
            WHEN expired.attempt >= :max_attempts
              OR now() >= COALESCE(expired.first_failed_at, now()) + :max_retry_age
                THEN now()
            ELSE NULL
        END
    FROM expired
    WHERE j.id = expired.id
      AND j.status = 'RUNNING'
      AND j.attempt_token = expired.attempt_token
    RETURNING j.id, j.status, j.attempt, j.terminal_reason
)
SELECT * FROM repaired;
```

Before returning a job with an external operation to `PENDING`, inspect `provider_operations`:

- `SUCCEEDED` — schedule/perform local finalization without repeating the effect.
- `INTENT` — query or repeat the exact request with the stable provider key.
- No operation row — retry if the attempt and wall-clock budgets remain.
- Different request hash — quarantine as a contract violation.

The SQL above is the fallback for jobs with no ambiguous external effect or after that ambiguity has been resolved.

---

## 4. The reconciler stops before it becomes an outage

Each run has both a batch limit and a pass limit:

```python
from collections.abc import Awaitable, Callable


async def reconcile_bounded(
    repair_batch: Callable[[int], Awaitable[int]],
    batch_size: int = 100,
    max_passes: int = 10,
) -> tuple[int, bool]:
    repaired = 0
    for _ in range(max_passes):
        count = await repair_batch(batch_size)
        repaired += count
        if count < batch_size:
            return repaired, False
    return repaired, True
```

The boolean means the pass limit was exhausted with full batches. That is an alert: backlog is at least the per-run safety bound and may be growing faster than repair. Run different inconsistency classes separately so one large expired-lease backlog cannot block missing-outbox detection.

**How you know it is working**: occasional repairs are expected; success is bounded oldest-inconsistency age, stable repair rate, and an alert when a class exceeds its normal baseline or hits its pass limit.

---

## 5. DLQ is evidence, not a disposal bin

Terminal work needs enough immutable context for an operator to decide whether redrive is safe:

```text
job_id, workflow_run_id, step
original operation/idempotency key
attempt count and first failure time
last error class and sanitized message
workflow state/version at failure
provider operation ID/result state
message/outbox IDs
terminal reason
payload reference + content hash, not an oversized secret payload
```

Redrive is a named operator command:

1. Identify and record the corrected cause: code version, credentials, input repair, or provider recovery.
2. Re-evaluate the current workflow state; old work may now be stale or cancelled.
3. Preserve the original operation key when repeating the same effect.
4. Create a new attempt or replacement job through a conditional transition.
5. Record actor, reason, old job, new job, and resulting workflow version.

⚠️ Copying a poison message back to the live queue with a new ID bypasses request-hash validation, workflow preconditions, and the audit trail.

⚠️ A DLQ with shorter effective retention than the investigation window deletes the only evidence before an operator sees it. Monitor oldest age and deletion/retention policy.

Do not auto-redrive permanent validation, authorization, or deterministic code failures. A retry budget that ends in an invisible terminal row is also incomplete; expose failed count, oldest age, and an inspected recovery action.

---

## 6. Observability follows stable identifiers across boundaries

Every log, metric, and trace includes the relevant subset:

```text
workflow_run_id   workflow_version   step
job_id            attempt            attempt_token
operation_key     provider_operation_id
message_id        outbox_id          delivery_receipt
worker_id         tenant_id          trace_id
```

The **default signals** are the first seven rows; add resource and cost signals as the workload grows.

| Signal | What it reveals | Default? |
|---|---|:---:|
| Oldest ready job/message age | User-visible starvation | ✓ |
| Ready depth and in-flight count | Backlog and capacity | ✓ |
| Success/failure/retry rate by step | Error concentration | ✓ |
| Lease expiry, heartbeat failure, lost-token writes | Worker death or event-loop stalls | ✓ |
| Oldest unpublished outbox age and confirm failures | Broken DB-to-broker handoff | ✓ |
| DLQ count and oldest age | Unremediated poison work | ✓ |
| Reconciliation findings/repairs by invariant | Hidden consistency gaps | ✓ |
| Provider latency/status/rate-limit count | Dependency health | |
| End-to-end workflow latency by terminal state | Business SLO | |
| Attempt duration and memory high-water | Pool sizing and leaks | |
| Idempotency attempts-to-effects ratio | Replay safety under failure | |
| Per-tenant oldest age | Fairness and noisy neighbors | |

Queue depth without age can look healthy while one tenant starves. Worker CPU without provider pool-wait time leads to the wrong scaling decision. Alert on business evidence and handoff gaps, not only runtime health.

---

## 7. Ordering and limits exist at several scopes

Messages arrive twice, late, and out of order. Include expected workflow version in delivery hints and let the authoritative transition reject stale work. Serialize only the entity that needs it through a partition key, advisory lock, or conditional version—not global single-threaded execution.

```text
per worker/event loop  → protects one process
per pod                → protects local pools and memory
global                 → protects provider/account/database quota
per tenant             → prevents one customer consuming the global budget
```

A local semaphore cannot enforce a global provider limit after horizontal scaling. Use a distributed limiter, centralized dispatch budget, provider-side quota partition, or a conservative per-pod allocation based on maximum replicas.

Strict priority can starve normal work. Measure oldest age per class and use reserved capacity or aging when one queue-selection rule cannot satisfy both latency and bulk throughput.

---

## 8. Deployments drain ownership before processes exit

The first four behaviors are required before production rollout:

- **Report not-ready before draining** so claims move elsewhere.
- **Stop claiming on `SIGTERM`** while existing attempts continue.
- **Keep heartbeat/visibility renewal alive during drain** until completion or deliberate release.
- **Use a termination grace period derived from normal attempt duration**, with checkpoint/retry for work that exceeds it.
- Size database/HTTP pools for per-pod concurrency and maximum replicas.
- Autoscale on oldest-ready age plus depth, capped by downstream quotas.
- Recycle leaking processes only through the same drain protocol.
- Keep secrets out of messages/logs and large artifacts behind immutable references/hashes.

⚠️ A readiness probe that stays green during shutdown lets a dying worker claim jobs it cannot finish.

⚠️ A liveness probe that kills a healthy slow task creates duplicate work. Liveness detects a wedged runtime; deadlines and leases govern attempts.

Do not call the system production-ready until an operator can answer: Which work is stuck? Who owns it? Can the effect repeat? What evidence makes retry, cancellation, or redrive safe?

---

**Next**: [Durable Fan-Out and Join](../07_durable_fanout_and_join.md)
