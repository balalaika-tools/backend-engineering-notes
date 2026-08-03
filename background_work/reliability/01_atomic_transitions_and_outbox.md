# Atomic Intent Prevents Work from Falling Between Systems

> **Who this is for**: Engineers connecting a domain database to a job table or external broker without losing or inventing work.

Before reading this, follow the row-by-row lifecycle in **[End-to-End Database-Backed Workflow](../state_machines/04_end_to_end_workflow.md)**.

---

## 1. Two correct writes can still produce an incorrect system

Approval commits, then the process crashes before it publishes generation work. In the reverse order, the broker accepts a message and the database transaction rolls back. Neither API call failed from its own point of view, but the combined result violates the workflow.

```text
DB first                               broker first
approve commits                        message publishes
process dies                           transaction rolls back
no message exists                      worker sees no approved run
```

The fix is to choose one commit point. When the domain database is authoritative, store the transition and **durable execution intent** in the same transaction. A database worker claims a job row directly; a broker publisher delivers an outbox row later.

> **The near-miss**: wrapping an `UPDATE` and an unconditional `INSERT` in `BEGIN`/`COMMIT` guarantees they commit together, but the insert still runs when an optimistic `UPDATE` affects zero rows. Transactional does not automatically mean conditional.

---

## 2. A data-modifying CTE makes downstream writes depend on the winner

For editorial approval, state, history, job, and outbox must share the compare-and-set result:

```sql
WITH updated AS (
    UPDATE workflow_runs
    SET state = 'GENERATION_QUEUED',
        version = version + 1,
        updated_at = now()
    WHERE id = :run_id
      AND state = 'WAITING_FOR_HUMAN_REVIEW'
      AND version = :expected_version
    RETURNING id, version, workflow_definition_version
),
history AS (
    INSERT INTO workflow_transitions (
        workflow_run_id, from_state, event, to_state,
        actor_type, actor_id, workflow_version, metadata
    )
    SELECT id,
           'WAITING_FOR_HUMAN_REVIEW',
           'approve',
           'GENERATION_QUEUED',
           'user',
           :reviewer_id,
           version,
           jsonb_build_object('reason', :reason)
    FROM updated
),
job AS (
    INSERT INTO jobs (
        id, workflow_run_id, step, status, idempotency_key, payload
    )
    SELECT :job_id,
           id,
           'generate_final',
           'PENDING',
           id::text || ':generate_final:v' || workflow_definition_version::text,
           jsonb_build_object('expected_workflow_version', version)
    FROM updated
    ON CONFLICT (idempotency_key) DO NOTHING
),
message AS (
    INSERT INTO outbox (
        id, aggregate_id, event_type, payload
    )
    SELECT :message_id,
           id,
           'generation.requested',
           jsonb_build_object(
               'job_id', :job_id,
               'workflow_run_id', id,
               'expected_workflow_version', version
           )
    FROM updated
    ON CONFLICT (id) DO NOTHING
)
SELECT id, version AS new_version FROM updated;
```

Zero final rows means nothing downstream received input. The caller returns a conflict and reloads the run. One returned row means the database contains both the new business fact and everything required to deliver its work.

> **Key insight**: The durable boundary is not “one transaction” in the abstract. Every dependent write must be mechanically downstream of the same successful precondition.

---

## 3. A job table and an outbox solve different handoffs

| Record | Authority | Consumer | Completion meaning |
|---|---|---|---|
| `jobs` | One requested execution and its attempts | Worker | The step's local execution status |
| `outbox` | One message still owed to a broker | Publisher | The broker confirmed receipt |

Use only a job row when workers poll the database. Use job plus outbox when the job row remains execution evidence but a broker wakes or routes workers. Use only an outbox event when the consumer owns its own job record and the producer should not track execution.

Do not let the broker message carry a copied workflow. Publish `message_id`, `job_id`, `workflow_run_id`, and expected version; the worker reloads authority and conditionally claims the job.

---

## 4. The publisher is a leased worker with publish-specific evidence

The outbox row needs a durable schedule, unique claim token, and confirmation state:

```sql
CREATE TABLE outbox (
    id UUID PRIMARY KEY,
    aggregate_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claim_token UUID,
    publisher_id TEXT,
    claim_expires_at TIMESTAMPTZ,
    last_error TEXT,
    last_confirm_error TEXT
);

CREATE INDEX outbox_ready_idx
    ON outbox (next_attempt_at, created_at)
    WHERE published_at IS NULL;
```

Claim only available publisher capacity:

```sql
WITH candidates AS (
    SELECT id
    FROM outbox
    WHERE published_at IS NULL
      AND next_attempt_at <= now()
      AND (claim_expires_at IS NULL OR claim_expires_at <= now())
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT :available_slots
)
UPDATE outbox AS o
SET claim_token = gen_random_uuid(),
    publisher_id = :publisher_id,
    claim_expires_at = now() + interval '30 seconds',
    attempts = attempts + 1
FROM candidates
WHERE o.id = candidates.id
RETURNING o.id, o.event_type, o.payload, o.claim_token;
```

Publish with `outbox.id` as the broker message ID. Mark success only after the broker's publish confirmation:

```sql
UPDATE outbox
SET published_at = now(),
    claim_token = NULL,
    publisher_id = NULL,
    claim_expires_at = NULL,
    last_error = NULL,
    last_confirm_error = NULL
WHERE id = :outbox_id
  AND published_at IS NULL
  AND claim_token = :claim_token
RETURNING id;
```

If the publisher crashes after broker confirmation but before this update, the claim expires and the row publishes again. That is deliberate at-least-once publication. The consumer deduplicates `message_id` or performs an idempotent job claim.

---

## 5. Failed publication returns to a durable backoff

A publish exception and a missing confirmation are different evidence. Persist both and release only the current claim:

```sql
UPDATE outbox
SET next_attempt_at = now() + (:backoff_seconds * interval '1 second'),
    last_error = :publish_error,
    last_confirm_error = :confirm_error,
    claim_token = NULL,
    publisher_id = NULL,
    claim_expires_at = NULL
WHERE id = :outbox_id
  AND published_at IS NULL
  AND claim_token = :claim_token
RETURNING id;
```

Zero rows means the publisher lost ownership. It must not report success or overwrite a newer publisher's evidence.

**How you know it is working**: track oldest-unpublished age, unpublished count, confirmation failures, attempts per message, and publish throughput. The silent failure is domain transitions continuing while oldest-unpublished age climbs; queue depth may remain zero because nothing reaches the queue.

---

## 6. Reconciliation proves the implication in both directions

Run bounded checks for:

- `*_QUEUED` workflow state without a matching job or outbox row.
- Unpublished outbox row whose `next_attempt_at` is old but no publisher is making progress.
- Published outbox row whose job never became claimable or terminal.
- Job/outbox record whose expected workflow version or state is incompatible.

Repair only when the missing intent can be recreated with the original deterministic key. Otherwise alert with run, job, message, and transition identifiers.

⚠️ Marking `published_at` before confirmation turns a network ambiguity into silent loss.

⚠️ Retrying a failed publish with a new message ID defeats consumer deduplication. Preserve the original outbox ID across attempts.

Do not add an outbox when producer state and work already share a database job table with no external broker. Do not assume an outbox creates exactly-once consumption; it closes the lost-publication gap and intentionally permits duplicates.

---

**Next**: [Leases, Heartbeats, and Fencing](02_leases_heartbeats_and_fencing.md)
