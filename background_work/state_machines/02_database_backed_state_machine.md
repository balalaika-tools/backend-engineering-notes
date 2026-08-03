# A Database-Backed State Machine Commits Decisions and Work Together

> **Who this is for**: Engineers implementing a low-to-medium-volume service workflow in the same relational database as the domain record.

Before reading this, implement named-event decisions as described in **[Application-Code Approaches](01_application_code_approaches.md)**.

---

## 1. A valid decision can still be lost between writes

The command service correctly derives `GENERATION_QUEUED`, commits the workflow row, then crashes before inserting the generation job. The state machine says generation is owed, but no worker will ever see it. Correct transition code is necessary; a shared atomic boundary makes it recoverable.

The repository default is:

```text
named event
  └── pure transition decision
        └── one database transaction
              ├── compare-and-set workflow row
              ├── append transition history
              └── insert job or outbox intent
```

The transaction never contains a provider call. It persists the decision and the obligation to execute; a worker performs external work later.

---

## 2. Separate run, execution intent, and evidence

The core schema stores a workflow definition version separately from the concurrency version and uses a unique attempt token for each job claim:

```sql
CREATE TABLE workflow_runs (
    id UUID PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    workflow_definition_version INTEGER NOT NULL,
    state TEXT NOT NULL,
    version BIGINT NOT NULL DEFAULT 0,
    cancellation_requested_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE jobs (
    id UUID PRIMARY KEY,
    workflow_run_id UUID REFERENCES workflow_runs(id),
    step TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    priority INTEGER NOT NULL DEFAULT 0,
    attempt_token UUID,
    worker_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL DEFAULT '{}',
    result_ref TEXT,
    first_failed_at TIMESTAMPTZ,
    last_error_class TEXT,
    last_error TEXT,
    terminal_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE TABLE workflow_transitions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workflow_run_id UUID NOT NULL REFERENCES workflow_runs(id),
    from_state TEXT NOT NULL,
    event TEXT NOT NULL,
    to_state TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    workflow_version BIGINT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workflow_run_id, workflow_version)
);

CREATE INDEX jobs_claimable_idx
    ON jobs (next_attempt_at, created_at)
    WHERE status = 'PENDING';

CREATE INDEX jobs_expired_lease_idx
    ON jobs (lease_expires_at)
    WHERE status = 'RUNNING';
```

`workflow_runs.state` is authoritative current state. `workflow_transitions` explains how it changed but does not rebuild it. `jobs` records owed and attempted execution; it is not a second workflow state machine.

> **Key insight**: The state row records what became true; the job or outbox row records what is now owed. A recoverable transition commits both facts or neither.

---

## 3. The command derives the target before opening the transaction

The API accepts `event=approve`, reviewer evidence, and `expected_version`. It does not accept a target state.

```text
POST /workflow-runs/run-42/commands
{
  "event": "approve",
  "expected_version": 7,
  "reviewer_id": "editor-7",
  "reason": "sources verified"
}
```

The application service follows this order:

1. Load `workflow_runs.state`, `version`, and `workflow_definition_version`.
2. Resolve the versioned transition graph.
3. Call `decide(current_state, "approve", evidence)`.
4. Receive only `to_state=GENERATION_QUEUED` and `ScheduleGeneration`.
5. Commit the conditional update, history, and job together.

If the event is illegal, return a domain conflict without opening a write transaction. If it is legal but the version changes before commit, the database compare-and-set rejects the stale decision.

---

## 4. One data-modifying CTE closes the losing race

The minimal compare-and-set is:

```sql
UPDATE workflow_runs
SET state = :derived_to_state,
    version = version + 1,
    updated_at = now()
WHERE id = :run_id
  AND state = :expected_from_state
  AND version = :expected_version
RETURNING id, version;
```

Zero returned rows means the command was stale or lost a race. Reload and return the actual state; do not blindly update to the requested target.

The hardened form ensures a losing compare-and-set cannot append evidence or create work:

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
next_job AS (
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
)
SELECT id, version AS new_version FROM updated;
```

Every insert selects from `updated`. If another command already changed version 7, `updated` returns no row, the inserts receive no input, and the final query returns no row.

⚠️ `BEGIN; UPDATE ...; INSERT ...; COMMIT;` is not sufficient when the insert is unconditional. A compare-and-set that affects zero rows does not abort the transaction; the unrelated insert still runs.

**How you know it is working**: a concurrent `approve` versus `cancel` test returns one success and one explicit conflict. The winning workflow version has exactly one matching history row, and only an `approve` winner has a generation job.

---

## 5. Cancellation is another named transition

Cancellation intent must use the same concurrency boundary and preserve authorization evidence:

```sql
WITH cancelled AS (
    UPDATE workflow_runs
    SET state = 'CANCELLED',
        version = version + 1,
        cancellation_requested_at = now(),
        updated_at = now()
    WHERE id = :run_id
      AND state = ANY(:cancellable_states)
      AND version = :expected_version
    RETURNING id, version
)
INSERT INTO workflow_transitions (
    workflow_run_id, from_state, event, to_state,
    actor_type, actor_id, workflow_version, metadata
)
SELECT id,
       :expected_state,
       'cancel',
       'CANCELLED',
       :actor_type,
       :actor_id,
       version,
       jsonb_build_object(
           'reason', :reason,
           'authorization_basis', :authorization_basis
       )
FROM cancelled
RETURNING workflow_version;
```

The command service derives `:cancellable_states` from the versioned transition graph and verifies the actor before executing SQL. A worker checks workflow state between safe units. Its later completion uses `WHERE workflow_runs.state = 'GENERATION_RUNNING' AND version = :worker_expected_version`; if cancellation won first, completion affects zero rows and cannot resurrect the run.

Cancellation cannot undo an already committed external effect. The transition may instead schedule a compensating job, such as `refund_charge`, with its own idempotency key and audit evidence.

---

## 6. Optimistic CAS, row locking, and a single writer solve different contention

Use optimistic CAS when conflicts are rare and the transition fits one conditional transaction. It keeps locks short and makes losers explicit.

Use `SELECT ... FOR UPDATE` when a command must inspect and update a small multi-row invariant, such as reserving one of several limited slots. Keep the transaction short and never call a provider while holding the lock.

Use a single writer per workflow key when command ordering is the dominant requirement and partitioning is stable. The cost is head-of-line blocking and a recovery protocol for the writer; the queue ordering rule becomes part of correctness.

| Symptom | Better fit |
|---|---|
| Rare `approve`/`cancel` race | Optimistic CAS |
| Multi-row capacity decision | Short row-locking transaction |
| Continuous commands for one hot aggregate | Partitioned single writer |

⚠️ Retrying a stale command without re-running the transition decision can apply evidence to a different state than the caller reviewed.

⚠️ A row lock protects database writes, not an external API call made after the transaction. External effects still need idempotency.

---

## 7. Definition changes need a compatibility policy

Resolve transition code using `workflow_definition_version`. The safest default for incompatible graph changes is to pin each run to the version recorded at creation and keep old handlers deployed until those runs terminate.

For a deliberate migration, atomically update the definition version and current state, then append a `workflow_migrated` history row containing old version, new version, migration code identifier, actor, and reason. Reconciliation should flag a run whose version has no deployed handler.

Do not use this model when authoritative history must be rebuilt into several projections, when every domain decision is naturally an immutable event, or when durable timers/signals/replay dominate the implementation. Continue to the event-sourced model or adopt a durable workflow engine.

---

**Next**: [Event-Sourced State Machines](03_event_sourced_state_machine.md)
