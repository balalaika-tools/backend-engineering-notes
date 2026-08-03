# Leases Recover Abandoned Work; Fencing Rejects the Old Owner

> **Who this is for**: Engineers implementing worker ownership for jobs that may outlive a process, connection, or deployment.

Before reading this, see how the job is created atomically in **[Atomic Transitions and Outbox](01_atomic_transitions_and_outbox.md)**.

---

## 1. `RUNNING` becomes permanent when its owner dies

Worker A changes a job from `PENDING` to `RUNNING`, then its container is killed. A status flag or a session lock cannot answer whether A will return, so the job remains stuck or an operator resets it blindly.

```text
Worker A claims job-55
  → jobs.status = RUNNING
  → A crashes
  → no finally block runs
  → job-55 stays RUNNING forever
```

A **lease** makes ownership temporary. The claim records an expiry; heartbeats move it forward; another worker may recover the job after the expiry.

```text
10:00:00  A claims until 10:01:30
10:00:30  A heartbeats until 10:02:00
10:00:41  A crashes
10:02:00  lease expires
10:02:01  B recovers and claims
```

That still leaves one danger: A may be partitioned rather than dead and return after B claims. Every attempt therefore gets a unique **attempt token** used as a fencing predicate.

> **The near-miss**: `worker_id` looks like a fencing value. A deployment can reuse the same pod name, process label, or logical worker ID, letting an old process match a later attempt. Keep `worker_id` for logs; generate a fresh token on every claim.

---

## 2. Claim only work that can execute now

The worker calculates its free slots before entering the claim transaction:

```sql
WITH candidates AS (
    SELECT id
    FROM jobs
    WHERE status = 'PENDING'
      AND next_attempt_at <= now()
    ORDER BY priority DESC, next_attempt_at, created_at
    FOR UPDATE SKIP LOCKED
    LIMIT :available_slots
)
UPDATE jobs AS j
SET status = 'RUNNING',
    attempt = attempt + 1,
    attempt_token = gen_random_uuid(),
    worker_id = :worker_id,
    lease_expires_at = now() + interval '90 seconds',
    started_at = COALESCE(started_at, now())
FROM candidates
WHERE j.id = candidates.id
RETURNING j.id, j.attempt, j.attempt_token, j.lease_expires_at;
```

One row might become:

```text
job-55 | RUNNING | attempt=1 | token-a | worker-A | expires=10:01:30
```

**Invariant**: each returned row immediately occupies one execution slot. No claimed job waits behind an in-memory semaphore while its lease is renewed.

`SKIP LOCKED` prevents claimers from waiting on one another; it does not make the selection fair. Keep a matching partial index, measure oldest-ready age by priority/tenant, and add aging or reserved capacity when strict priority starves work.

---

## 3. Heartbeat success is a precondition for continuing

Renew with database time and the unique token:

```sql
UPDATE jobs
SET lease_expires_at = now() + interval '90 seconds'
WHERE id = :job_id
  AND status = 'RUNNING'
  AND attempt_token = :attempt_token
  AND lease_expires_at > now()
RETURNING lease_expires_at;
```

Exactly one returned row means the attempt still owns the lease. Zero rows means it expired, was recovered, was cancelled, or already terminated. The worker cancels its local operation and enters a fenced state in which it may record diagnostic evidence but may not mutate job or workflow rows.

Run heartbeats in a lightweight task independent from the provider call. The work supervisor contract is:

```text
provider task ───────────────┐
                            ├── first failure cancels the sibling
heartbeat task ──────────────┘
                                  │
                                  └── no completion unless last heartbeat succeeded
```

⚠️ Logging a heartbeat exception and letting work continue creates the exact stale-worker race the lease was supposed to control.

Choose a lease several times longer than normal heartbeat jitter and shorter than the maximum acceptable recovery delay. A common starting ratio is heartbeat every one-third of the lease, then tune from measured scheduler pauses, database latency, and provider behavior. Use the database's clock for claim, heartbeat, and expiry comparisons so worker clock skew cannot steal or extend ownership.

---

## 4. Recovery replaces the token before work resumes

When A's lease expires, a bounded reconciler first returns the job to `PENDING`:

```sql
WITH expired AS (
    SELECT id, attempt_token
    FROM jobs
    WHERE status = 'RUNNING'
      AND lease_expires_at <= now()
    ORDER BY lease_expires_at
    FOR UPDATE SKIP LOCKED
    LIMIT :repair_limit
)
UPDATE jobs AS j
SET status = 'PENDING',
    next_attempt_at = now(),
    attempt_token = NULL,
    worker_id = NULL,
    lease_expires_at = NULL,
    last_error_class = COALESCE(last_error_class, 'LEASE_EXPIRED'),
    last_error = COALESCE(last_error, 'worker stopped heartbeating')
FROM expired
WHERE j.id = expired.id
  AND j.status = 'RUNNING'
  AND j.attempt_token = expired.attempt_token
RETURNING j.id;
```

Worker B then uses the normal claim and receives a new token:

```text
job-55 | RUNNING | attempt=2 | token-b | worker-B | expires=10:03:31
```

Do not reuse `token-a`, even when the same worker process immediately retries. The token identifies one ownership epoch, not an executor identity.

> **Key insight**: Expiry decides when another attempt may start; the token decides whose late writes are still valid. A lease without fencing recovers availability but not correctness.

---

## 5. Every terminal write uses the same fence

Completion is conditional on current ownership:

```sql
UPDATE jobs
SET status = 'SUCCEEDED',
    result_ref = :result_ref,
    finished_at = now(),
    attempt_token = NULL,
    worker_id = NULL,
    lease_expires_at = NULL
WHERE id = :job_id
  AND status = 'RUNNING'
  AND attempt_token = :attempt_token
  AND lease_expires_at > now()
RETURNING id;
```

Retry scheduling, permanent failure, workflow completion, and compensation scheduling use the same token in their transaction. Application code must check that exactly one row changed before acknowledging the message or reporting success.

```text
10:02:01  B owns token-b
10:02:05  partitioned A returns with token-a
10:02:06  A completion UPDATE → 0 rows
10:02:07  B completion UPDATE → 1 row
```

Preserve A's external response in logs or a quarantine record; do not overwrite B. [Idempotency and External Effects](03_idempotency_and_external_effects.md) decides whether the provider result can be adopted safely.

---

## 6. A lease does not make a provider effect exactly once

A loses its lease while an HTTP request is already inside the provider. B starts after expiry and sends the same business request. Database fencing rejects A's local completion, but it cannot recall either network request.

Use a stable provider idempotency key across `token-a` and `token-b`. The attempt token protects local writes; the operation key protects the business effect. They must not be the same value because their lifetimes differ.

⚠️ A lease shorter than routine provider latency causes healthy work to be recovered and replayed. Heartbeat it or split the task; do not merely increase it beyond the maximum acceptable recovery delay.

⚠️ A lease longer than deployment and incident recovery targets leaves dead work invisible for too long. Measure time-to-reclaim, not only heartbeat success.

---

## 7. A failure-injection test proves the stale owner loses

The following PostgreSQL script is self-contained. It pauses A beyond expiry, recovers and claims as B, then asserts that A cannot complete:

```sql
CREATE TEMP TABLE lease_test_jobs (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    attempt_token TEXT,
    lease_expires_at TIMESTAMPTZ,
    result_ref TEXT
);

INSERT INTO lease_test_jobs (id, status) VALUES (55, 'PENDING');

UPDATE lease_test_jobs
SET status = 'RUNNING',
    attempt = 1,
    attempt_token = 'token-a',
    lease_expires_at = clock_timestamp() + interval '100 milliseconds'
WHERE id = 55 AND status = 'PENDING';

SELECT pg_sleep(0.15);

UPDATE lease_test_jobs
SET status = 'PENDING', attempt_token = NULL, lease_expires_at = NULL
WHERE id = 55
  AND status = 'RUNNING'
  AND attempt_token = 'token-a'
  AND lease_expires_at <= clock_timestamp();

UPDATE lease_test_jobs
SET status = 'RUNNING',
    attempt = 2,
    attempt_token = 'token-b',
    lease_expires_at = clock_timestamp() + interval '10 seconds'
WHERE id = 55 AND status = 'PENDING';

DO $$
DECLARE
    changed INTEGER;
BEGIN
    UPDATE lease_test_jobs
    SET status = 'SUCCEEDED', result_ref = 'stale-a'
    WHERE id = 55 AND status = 'RUNNING' AND attempt_token = 'token-a';
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 0 THEN
        RAISE EXCEPTION 'stale token-a completed the job';
    END IF;

    UPDATE lease_test_jobs
    SET status = 'SUCCEEDED', result_ref = 'current-b'
    WHERE id = 55 AND status = 'RUNNING' AND attempt_token = 'token-b';
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed <> 1 THEN
        RAISE EXCEPTION 'current token-b failed to complete the job';
    END IF;
END $$;

SELECT status, attempt, result_ref FROM lease_test_jobs WHERE id = 55;
```

The final row is `SUCCEEDED | 2 | current-b`. If it contains `stale-a`, any completion path omitted the token predicate.

**How you know it is working**: token-mismatch updates stay at zero, expired-lease recovery time stays within its SLO, and lost-heartbeat metrics correlate with worker restarts or event-loop stalls.

Do not use leases to coordinate a short database-only transaction; a row lock already has a failure-aware owner in the database session. Do not rely on leases alone for irreversible external effects.

---

**Next**: [Idempotency and External Effects](03_idempotency_and_external_effects.md)
