# A Durable Job Is the Smallest Useful Background-Work System

> **Who this is for**: Backend engineers who need one independent task to survive request and worker-process loss, but do not yet need a multi-step business workflow.

Before reading this, understand the responsibility boundaries in **[Background Work Overview](01_overview.md)** and confirm that the work is still one task with **[When a Task Becomes a Workflow](02_when_a_task_becomes_a_workflow.md)**.

---

## 1. Returning `202` creates an obligation the web process cannot own

An API accepts a document for parsing, starts a coroutine, and returns `202 Accepted`. The pod restarts after the response, so the coroutine and the only record of the request disappear. The API promised eventual work without persisting that promise.

The smallest durable design puts one job row between the request and the worker:

```text
POST /document-parses
        │
        ├── commit one PENDING job
        └── 202 + Location: /document-parses/job-42
                              │
worker polls ──claims─────────┘
        │
        ├── RUNNING → write result → SUCCEEDED
        └── failure → PENDING at next_attempt_at, or FAILED
                              │
GET /document-parses/job-42 ──┘
```

The database row is the handoff. The API does not need a worker to be online, and the worker does not need the original request process to survive.

> **The near-miss**: a broker message looks like the job itself. It is usually only delivery. The durable job row owns whether work is still owed, which attempt owns it, and where its result lives.

---

## 2. One row can own submission, attempts, and result lookup

Start with four states rather than a workflow-sized graph:

| State | Meaning | Caller sees |
|---|---|---|
| `PENDING` | Work is owed; `next_attempt_at` decides when it is claimable | `202`, including the next eligible time after a retry |
| `RUNNING` | One leased attempt currently owns execution | `202`, including attempt and lease evidence |
| `SUCCEEDED` | The result reference committed | `200` with the result or a download URL |
| `FAILED` | The retry budget ended or the input is permanently invalid | Terminal error with a stable error class |

The minimal PostgreSQL schema keeps the input outside the queue row and gives request replay a stable key:

```sql
CREATE TABLE document_parse_jobs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    request_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    input_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempt_token UUID,
    lease_expires_at TIMESTAMPTZ,
    result_ref TEXT,
    last_error_class TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    UNIQUE (tenant_id, request_key),
    CHECK ((status = 'RUNNING') = (attempt_token IS NOT NULL)),
    CHECK ((status = 'RUNNING') = (lease_expires_at IS NOT NULL)),
    CHECK ((status = 'SUCCEEDED') = (result_ref IS NOT NULL))
);

CREATE INDEX document_parse_jobs_ready_idx
    ON document_parse_jobs (next_attempt_at, created_at)
    WHERE status = 'PENDING';
```

Store a document in object storage first, then put its immutable reference and content-derived hash in the row. Large request bodies do not belong in a hot polling index.

> **Key insight**: durability begins when one record can answer “is work still owed?” after every participating process has disappeared.

---

## 3. Submission replay converges on one job

Require an `Idempotency-Key`-style `request_key` from the caller. Hash the normalized task input—not authentication headers or timestamps—and create the row in a transaction:

```sql
INSERT INTO document_parse_jobs (
    id, tenant_id, request_key, request_hash, input_ref, status
)
VALUES (
    :job_id, :tenant_id, :request_key, :request_hash, :input_ref, 'PENDING'
)
ON CONFLICT (tenant_id, request_key) DO UPDATE
SET request_key = EXCLUDED.request_key
RETURNING id,
          status,
          request_hash = :request_hash AS request_matches;
```

The no-op update makes concurrent submissions return the same locked row. The application checks `request_matches` before committing:

- `true`: return the existing job and the same `Location` header.
- `false`: roll back and return an idempotency-key conflict; the same key was reused for different input.

One visible baseline trace is enough to prove the contract:

```text
POST key=parse-acme-17, hash=sha256:aaa → 202, job-42
POST key=parse-acme-17, hash=sha256:aaa → 202, job-42
POST key=parse-acme-17, hash=sha256:bbb → 409, no new job
```

**Worked signal**: all same-key/same-hash responses point to one job ID, and the table contains one row. The silent failure is two rows for one logical request, usually because the unique key was generated inside each retry rather than supplied by the caller.

---

## 4. Claiming gives one attempt temporary ownership

Each free worker slot claims at most one ready row. `SKIP LOCKED` lets competing workers move past a row another transaction already selected:

```sql
WITH candidate AS (
    SELECT id
    FROM document_parse_jobs
    WHERE status = 'PENDING'
      AND next_attempt_at <= now()
    ORDER BY next_attempt_at, created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE document_parse_jobs AS j
SET status = 'RUNNING',
    attempt = attempt + 1,
    attempt_token = gen_random_uuid(),
    lease_expires_at = now() + interval '90 seconds'
FROM candidate
WHERE j.id = candidate.id
RETURNING j.id, j.input_ref, j.attempt, j.attempt_token, j.lease_expires_at;
```

The returned token identifies this ownership epoch. Every heartbeat, retry, and completion checks it; a worker process name is useful in logs but is not unique enough to fence a late attempt.

For work longer than the lease, heartbeat while the parser runs. If a heartbeat updates zero rows, stop local work and make no terminal database write. The complete ownership protocol—including heartbeat supervision and stale-worker recovery—belongs to **[Leases, Heartbeats, and Fencing](reliability/02_leases_heartbeats_and_fencing.md)**.

**Worked signal**: racing two database connections for one row returns one job to exactly one connection. If both workers begin parsing, the claim is not one atomic statement or the worker started before commit.

---

## 5. Completion publishes a result only for the current attempt

For this task, make replay harmless by writing the parsed artifact to a deterministic key such as `document-parses/{job_id}/result.json`. The same input can replace the same object; it cannot create a second business effect.

After the object store confirms the write, complete behind the token:

```sql
UPDATE document_parse_jobs
SET status = 'SUCCEEDED',
    result_ref = :result_ref,
    finished_at = now(),
    attempt_token = NULL,
    lease_expires_at = NULL,
    last_error_class = NULL
WHERE id = :job_id
  AND status = 'RUNNING'
  AND attempt_token = :attempt_token
  AND lease_expires_at > now()
RETURNING id, result_ref;
```

One returned row means the result is authoritative. Zero rows means the attempt lost ownership; it must not report success or acknowledge a delivery as though it completed the job.

The result endpoint reads the same row:

```http
GET /document-parses/job-42

HTTP/1.1 200 OK
Content-Type: application/json

{"job_id":"job-42","status":"SUCCEEDED","result_ref":"s3://results/document-parses/job-42/result.json"}
```

If the task sends email, charges money, or calls another non-transactional system, a deterministic object key is not enough. Use the provider's stable operation key and local effect evidence from **[Idempotency and External Effects](reliability/03_idempotency_and_external_effects.md)**.

---

## 6. Retry reschedules the same obligation instead of creating a new one

A transient failure returns the current attempt to `PENDING` with a durable future time. A permanent error or exhausted budget moves it to `FAILED`:

```sql
UPDATE document_parse_jobs
SET status = CASE
        WHEN attempt >= max_attempts OR :is_permanent THEN 'FAILED'
        ELSE 'PENDING'
    END,
    next_attempt_at = CASE
        WHEN attempt >= max_attempts OR :is_permanent THEN next_attempt_at
        ELSE now() + (:backoff_seconds * interval '1 second')
    END,
    last_error_class = :error_class,
    finished_at = CASE
        WHEN attempt >= max_attempts OR :is_permanent THEN now()
        ELSE NULL
    END,
    attempt_token = NULL,
    lease_expires_at = NULL
WHERE id = :job_id
  AND status = 'RUNNING'
  AND attempt_token = :attempt_token
RETURNING id, status, attempt, next_attempt_at;
```

Use the same `job_id`, `request_key`, and deterministic result/effect key on every attempt. Creating a fresh job during retry loses the original budget, status URL, and deduplication identity.

If a worker disappears, a bounded recovery pass moves only expired `RUNNING` jobs back to `PENDING`; the next claim generates a new token. Retry classification, jitter, attempt limits, elapsed-time budgets, and cancellation are expanded in **[Retries, Timeouts, and Cancellation](reliability/04_retries_timeouts_and_cancellation.md)**.

**Worked signal**: a retry is invisible before `next_attempt_at`, becomes claimable afterward, increments `attempt`, and still resolves through `/document-parses/job-42`. If a second job ID appears, retry was implemented as resubmission.

---

## 7. Operate the lifecycle before adding workflow machinery

The minimum useful dashboard has four signals:

| Signal | Healthy evidence | Failure it exposes |
|---|---|---|
| Oldest ready age | Falls when worker capacity is added | Claim filter, index, or capacity defect |
| Expired running count | Usually zero; repaired rows are explainable | Dead workers or missing heartbeats |
| Attempts per terminal job | Stable for each error class | Retry storm or bad permanence classification |
| Terminal counts and latency | `PENDING → terminal` meets the stated SLO | A queue that is durable but not making progress |

Test one crash after claim and one crash after the result object is written but before completion. After recovery, the job must reach one terminal state, keep one stable result location, and leave no permanently `RUNNING` row.

⚠️ A lease without a token allows the old worker to overwrite the result after a replacement attempt starts.

⚠️ A retry with a new operation key can duplicate external effects even though the job table itself looks correct.

⚠️ An unbounded polling query can turn the primary database into the bottleneck; keep the partial ready index and claim only real execution capacity.

Do not add workflow state merely to represent worker attempts. Move to a workflow when users act on intermediate states, multiple steps branch or join, timers and signals outlive attempts, or compensation becomes part of the business contract. At that point continue to **[State-Machine Design](03_state_machine_design.md)**.

**Stop here if** one independent task, one status URL, bounded retry, and replay-safe output satisfy the product. Use the [Decision Guide](09_decision_guide.md) to validate the transport and execution choice; continue into state machines and reliability deep dives only when a specific failure or lifecycle requirement justifies them.

---

**Next**: [Part 9: Decision Guide](09_decision_guide.md)
