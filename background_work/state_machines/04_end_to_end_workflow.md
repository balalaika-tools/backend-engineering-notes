# One Workflow, From API Command to Crash Recovery

> **Who this is for**: Engineers assembling a database-backed state machine, broker delivery, and workers into one recoverable lifecycle.

Before reading this, understand the schema and compare-and-set boundary in **[Database-Backed State Machines](02_database_backed_state_machine.md)**.

---

## 1. Every component closes a different crash window

The editorial workflow has already completed research and is waiting for approval. Approval should eventually generate a final artifact through an external provider. The happy path is short; the design exists for the points where a process can disappear:

```text
API command
  → validate named event
  → one DB transaction creates state + history + job + outbox
  → outbox publisher polls committed unpublished rows
  → broker delivers an ID-only hint
  → worker conditionally claims the authoritative DB job
  → heartbeat
  → call provider with idempotency key
  → persist provider result
  → conditional completion
  → retry or terminal failure
  → reconciliation after worker crash
```

| Component | What it contributes | Why the state machine alone is insufficient |
|---|---|---|
| Transition function | Decides whether a named event is legal and derives the target | It does not survive a process exit |
| Database transaction | Preserves the decision and evidence atomically | It does not execute external work |
| Job row | Records one durable execution obligation and its attempts | It does not deliver work to remote workers by itself |
| Outbox + broker | Transports an ID-only delivery hint after commit | Broker delivery is not authoritative business state |
| Lease + attempt token | Gives one attempt temporary database ownership | Ownership can expire while an old process still runs |
| Provider idempotency | Makes a replayed business effect return the original outcome | A lease cannot make an external call exactly once |
| Durable retry policy | Chooses whether and when another attempt may start | Immediate in-memory retry disappears on crash and amplifies outages |
| Reconciler | Finds silence and contradictions outside normal handlers | No exception handler runs after a process is killed |
| Transition history | Explains which actor and evidence changed the lifecycle | Current state alone cannot reconstruct a race |

### This walkthrough deliberately uses hybrid dispatch

The database does not send anything to the broker by itself, and the API does not commit the job and then perform a separate broker publish. The API writes only to PostgreSQL. A separate long-running **outbox publisher** polls committed outbox rows, publishes each ID-only message, waits for broker confirmation, and then marks that row as published.

```text
API
 │  one DB transaction
 ▼
PostgreSQL
 ├── workflow state + history
 ├── authoritative job row
 └── outbox row ◄──polls── outbox publisher
                                  │ publishes IDs
                                  ▼
                                broker
                                  │ delivers hint
                                  ▼
                                worker
                                  │ conditional claim
                                  ▼
                            authoritative job row
```

The publisher normally runs as its own service or container beside the API and workers. A request-scoped FastAPI `BackgroundTasks` callback is not a substitute: if the API process dies, the in-memory callback disappears, whereas an independent publisher can rediscover the committed outbox row.

Keep these two statements separate:

```text
broker message: "job-55 may be ready; check it"
database claim: "this attempt now has permission to execute job-55"
```

The worker never executes merely because it received a message. It uses `job_id` to attempt the database claim; a duplicate or stale hint that matches no eligible row grants no ownership.

### The simpler variant lets workers fetch jobs directly

The broker and outbox are optional dispatch machinery, not requirements of the lease, fencing, retry, or idempotency model used later in this walkthrough.

| Question | DB polling | Hybrid dispatch used below |
|---|---|---|
| What the API commits | Workflow state, history, and job | Workflow state, history, job, and outbox row |
| How work is discovered | Each worker scans eligible `PENDING` jobs | Publisher scans outbox; broker wakes or routes a worker |
| Where ownership is granted | Conditional DB claim | The same conditional DB claim |
| Main advantage | Few components and one durable system | Fast dispatch, burst buffering, queue-based distribution and autoscaling |
| Main cost | Polling latency and idle DB queries | Publisher, broker, duplicate delivery, and more monitoring |

In the DB-only variant, a worker repeats a bounded claim query, usually every few seconds with jitter. This single-job query shows the mechanism:

```sql
WITH candidate AS (
    SELECT id
    FROM jobs
    WHERE status = 'PENDING'
      AND next_attempt_at <= now()
    ORDER BY next_attempt_at, created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE jobs AS j
SET status = 'RUNNING',
    attempt = attempt + 1,
    attempt_token = gen_random_uuid(),
    worker_id = :worker_id,
    lease_expires_at = now() + interval '90 seconds'
FROM candidate
WHERE j.id = candidate.id
RETURNING j.*;
```

`SKIP LOCKED` lets concurrent workers skip a row another worker is claiming instead of waiting for it. In this workflow, the same short transaction must also compare-and-set `workflow_runs` and append the claim transition shown in §6; the query above isolates only the fetching mechanism.

For a workload on the order of 10–50 jobs per day, start with DB polling unless the dispatch-latency requirement says otherwise. The database is already authoritative, and the polling load is normally negligible. Add the outbox and broker when bursts, many workers or worker types, near-immediate dispatch, or queue-depth autoscaling justify the extra failure surface. The hybrid design reduces idle polling by execution workers, but it does not remove database work: the publisher still polls in batches and every worker still claims and updates its job in the database.

> **Key insight**: Recovery is a chain of evidence. Each durable row proves just enough for the next mechanism to continue without trusting the process that disappeared.

---

## 2. The starting rows make the precondition explicit

The API read model returns state and version to the review client:

```text
workflow_runs
┌────────┬──────────────────────────┬─────────┬────────────────────┐
│ id     │ state                    │ version │ definition_version │
├────────┼──────────────────────────┼─────────┼────────────────────┤
│ run-42 │ WAITING_FOR_HUMAN_REVIEW │ 7       │ 3                  │
└────────┴──────────────────────────┴─────────┴────────────────────┘

workflow_transitions (last row)
┌────────┬─────────┬────────────────────┬──────────────────────────┐
│ run_id │ version │ event              │ to_state                 │
├────────┼─────────┼────────────────────┼──────────────────────────┤
│ run-42 │ 7       │ research_succeeded │ WAITING_FOR_HUMAN_REVIEW │
└────────┴─────────┴────────────────────┴──────────────────────────┘

jobs:               no generation row
outbox:             no generation row
provider_operations: no generation row
```

**Invariant**: version 7 and the final history row describe the same durable state; no generation intent exists before approval.

**Crash here**: nothing is running, so restart changes nothing.

**Recovery mechanism**: the client can resubmit a command against version 7.

**Verification**: stop every service, restart only the API, and confirm the read model still offers `approve` and `cancel`—never `generation_succeeded`.

---

## 3. The API validates a named event, not a target state

The client submits:

```text
command_id:      cmd-91
event:           approve
expected_version: 7
reviewer_id:     editor-7
reason:          sources verified
```

Application code loads definition version 3 and derives:

```text
from_state: WAITING_FOR_HUMAN_REVIEW
to_state:   GENERATION_QUEUED
command:    ScheduleGeneration
job_key:    run-42:generate_final:v3
```

**Rows after validation**: unchanged. Validation is a pure decision.

**Invariant**: only the version-3 transition graph can derive the target and operation key.

**Crash here**: no durable state changed; the same command can be submitted again.

**Recovery mechanism**: command retry plus the upcoming compare-and-set and unique keys.

**Verification**: submit `approve` from `NEW` and submit an arbitrary `to_state`; both are rejected before the repository's write method is called.

---

## 4. One transaction creates state, history, job, and outbox

The approval transaction uses a data-modifying CTE. Every insert selects from the successful `UPDATE`, so a lost version race creates no downstream work.

```text
workflow_runs
┌────────┬───────────────────┬─────────┐
│ id     │ state             │ version │
├────────┼───────────────────┼─────────┤
│ run-42 │ GENERATION_QUEUED │ 8       │
└────────┴───────────────────┴─────────┘

workflow_transitions (new row)
┌────────┬─────────┬─────────┬───────────────────┬──────────┐
│ run_id │ version │ event   │ to_state          │ actor    │
├────────┼─────────┼─────────┼───────────────────┼──────────┤
│ run-42 │ 8       │ approve │ GENERATION_QUEUED │ editor-7 │
└────────┴─────────┴─────────┴───────────────────┴──────────┘

jobs
┌────────┬────────┬────────────────┬─────────┬────────────────────────────┐
│ id     │ run_id │ step           │ status  │ idempotency_key            │
├────────┼────────┼────────────────┼─────────┼────────────────────────────┤
│ job-55 │ run-42 │ generate_final │ PENDING │ run-42:generate_final:v3   │
└────────┴────────┴────────────────┴─────────┴────────────────────────────┘

outbox
┌────────┬────────┬──────────────────────┬──────────────┐
│ id     │ run_id │ event_type           │ published_at │
├────────┼────────┼──────────────────────┼──────────────┤
│ msg-18 │ run-42 │ generation.requested │ NULL         │
└────────┴────────┴──────────────────────┴──────────────┘
```

This compact snapshot omits the publisher's scheduling columns. `available_at` defaults to `now()` for the initial hint; a future retry sets it to the job's `next_attempt_at`, and the publisher selects only rows whose `available_at <= now()`.

**Invariant**: `GENERATION_QUEUED` implies exactly one durable generation intent. The job key and message ID are unique.

**Crash before commit**: no row changes. **Crash after commit**: all four changes remain.

The API stops after this commit. It does not call the broker as a second side effect; that would recreate the crash window the outbox exists to close.

**Recovery mechanism**: command retry sees version 8 and returns the already-applied outcome by `command_id`; the independently running outbox publisher discovers `published_at IS NULL`.

**Verification**: kill the API at every injected statement boundary. Assert the database contains either all four new facts or none, never a queued workflow without job/outbox evidence.

See [Atomic Transitions and Outbox](../reliability/01_atomic_transitions_and_outbox.md) for the SQL and publisher contract.

---

## 5. Publishing transports a hint and may happen twice

The independently running outbox publisher polls for due unpublished rows, claims `msg-18`, publishes `{message_id, job_id, run_id}`, waits for broker confirmation, then marks the row:

```text
outbox
┌────────┬──────────┬─────────────────────────┬──────────┬────────────┐
│ id     │ attempts │ published_at            │ claimed  │ last_error │
├────────┼──────────┼─────────────────────────┼──────────┼────────────┤
│ msg-18 │ 1        │ 2026-08-03T10:00:01Z    │ NULL     │ NULL       │
└────────┴──────────┴─────────────────────────┴──────────┴────────────┘

broker message
{message_id: msg-18, job_id: job-55, run_id: run-42}
```

**Invariant**: the message contains IDs, not a copied workflow object. The database remains authoritative.

**Crash after publish but before marking**: `msg-18` is published again when the claim expires.

**Recovery mechanism**: at-least-once publication plus conditional job claim; duplicate hints cannot create another job.

**Verification**: force the broker-confirm path to succeed and the database mark to fail. Deliver both copies and assert only one attempt owns `job-55`.

---

## 6. Claiming creates one unique attempt token

After the broker delivers `msg-18`, the worker uses `job_id=job-55` as a lookup key, not as permission to execute. It has one free execution slot, so it conditionally claims one row. The claim changes job and workflow state in one short transaction and generates a fresh UUID token:

```text
workflow_runs
┌────────┬────────────────────┬─────────┐
│ id     │ state              │ version │
├────────┼────────────────────┼─────────┤
│ run-42 │ GENERATION_RUNNING │ 9       │
└────────┴────────────────────┴─────────┘

jobs
┌────────┬─────────┬─────────┬───────────────┬──────────┬──────────────────────┐
│ id     │ status  │ attempt │ attempt_token │ worker   │ lease_expires_at     │
├────────┼─────────┼─────────┼───────────────┼──────────┼──────────────────────┤
│ job-55 │ RUNNING │ 1       │ token-a       │ worker-A │ 2026-08-03T10:01:31Z │
└────────┴─────────┴─────────┴───────────────┴──────────┴──────────────────────┘

workflow_transitions (new row)
run-42 | version 9 | claim_generation | GENERATION_RUNNING | token-a
```

**Invariant**: only predicates containing `job_id + status=RUNNING + attempt_token=token-a + unexpired lease` may heartbeat, fail, or complete this attempt. `worker-A` is observability metadata, not fencing identity.

**Crash during claim transaction**: no ownership is granted. **Crash after commit**: ownership expires at the recorded time.

**Recovery mechanism**: lease expiry and reconciliation create a new attempt with `token-b`.

**Verification**: race two claims for `job-55`; one returns the row and the other returns zero. Claim only as many rows as the worker has free execution slots.

See [Leases, Heartbeats, and Fencing](../reliability/02_leases_heartbeats_and_fencing.md).

---

## 7. Heartbeats preserve ownership only while they succeed

Worker A renews every 30 seconds for a 90-second lease:

```text
jobs after heartbeat
job-55 | RUNNING | token-a | worker-A | lease_expires_at=10:02:01Z
```

**Invariant**: heartbeat updates exactly one unexpired row for `token-a`. A zero-row update means ownership is already lost.

**Crash before heartbeat**: no renewal occurs and the existing lease expires. **Heartbeat task fails while work continues**: the attempt is unsafe even if provider code still runs.

**Recovery mechanism**: the worker cancels local execution on heartbeat failure and refuses all later commits; the reconciler reclaims after expiry.

**Verification**: make heartbeat return zero while the provider coroutine is paused. Assert the coroutine is cancelled and completion is never attempted with assumed ownership.

---

## 8. Provider idempotency closes the ambiguous-effect window

Before calling the provider, Worker A inserts a local operation intent:

```text
provider_operations
┌────────────────────────────┬────────┬────────┬──────────────┬────────────┐
│ idempotency_key            │ run_id │ state  │ request_hash │ provider_id│
├────────────────────────────┼────────┼────────┼──────────────┼────────────┤
│ run-42:generate_final:v3   │ run-42 │ INTENT │ sha256:6ac…  │ NULL       │
└────────────────────────────┴────────┴────────┴──────────────┴────────────┘
```

It sends the same key and the exact hashed request to the provider. An existing local key is reused only when its persisted hash equals the new hash.

**Invariant**: one business operation key always identifies one request meaning. Attempt number and lease token never enter the provider key.

**Crash before the provider receives the call**: retry sends the same key. **Crash after provider success but before local result**: retry sends the same key and the provider returns the original operation.

**Recovery mechanism**: provider idempotency plus local intent and provider lookup/retry policy.

**Verification**: inject a crash after provider commit, redeliver, and assert two HTTP attempts but one provider operation. Reuse the key with changed input and assert a conflict before any provider call.

See [Idempotency and External Effects](../reliability/03_idempotency_and_external_effects.md).

---

## 9. Persisting the provider result turns ambiguity into evidence

After the provider returns, Worker A stores the terminal result before completing the workflow:

```text
provider_operations
┌──────────────────────────┬───────────┬─────────────┬──────────────────────────────────┐
│ idempotency_key          │ state     │ provider_id │ result_ref                       │
├──────────────────────────┼───────────┼─────────────┼──────────────────────────────────┤
│ run-42:generate_final:v3 │ SUCCEEDED │ gen-884     │ s3://results/run-42/final.pdf   │
└──────────────────────────┴───────────┴─────────────┴──────────────────────────────────┘
```

**Invariant**: no workflow can become `COMPLETED` without durable provider identity and result evidence.

**Crash before this write**: recovery repeats or looks up the provider operation with the same key. **Crash after this write**: recovery finalizes locally without repeating the external effect.

**Recovery mechanism**: operation reconciliation distinguishes `INTENT` from `SUCCEEDED`.

**Verification**: kill the worker after this commit. Assert provider call count stays one and a later finalizer adopts `gen-884`.

---

## 10. Completion is conditional on both lease and workflow state

One transaction verifies `token-a` still owns the job and workflow version 9 is still `GENERATION_RUNNING`, then commits terminal evidence:

```text
workflow_runs
run-42 | COMPLETED | version=10

jobs
job-55 | SUCCEEDED | attempt=1 | attempt_token=NULL | result_ref=s3://results/run-42/final.pdf

workflow_transitions (new row)
run-42 | version 10 | generation_succeeded | COMPLETED | token-a

provider_operations
run-42:generate_final:v3 | SUCCEEDED | gen-884 | s3://results/run-42/final.pdf
```

**Invariant**: completion affects all local terminal records or none, and only the current attempt may commit it.

**Crash before commit**: the provider result remains recoverable and the workflow stays running. **Crash after commit**: a duplicate completion matches zero rows and is treated as already terminal, not success by assumption.

**Recovery mechanism**: conditional finalization uses the persisted provider result.

**Verification**: pause Worker A, expire/reclaim as `token-b`, then release A. A's completion affects zero rows; only B or the reconciler can commit version 10.

---

## 11. Retry returns owned work to a durable schedule

Suppose the provider returns a classified transient `503` before creating an effect. Worker A conditionally moves its attempt back to pending:

```text
workflow_runs
run-42 | GENERATION_QUEUED | version=10

jobs
┌────────┬─────────┬─────────┬───────────────┬─────────────┬──────────────────────┐
│ id     │ status  │ attempt │ attempt_token │ error_class │ next_attempt_at      │
├────────┼─────────┼─────────┼───────────────┼─────────────┼──────────────────────┤
│ job-55 │ PENDING │ 1       │ NULL          │ TRANSIENT   │ 2026-08-03T10:01:47Z │
└────────┴─────────┴─────────┴───────────────┴─────────────┴──────────────────────┘

outbox (new row in the hybrid variant)
msg-19 | generation.retry_requested | available_at=2026-08-03T10:01:47Z | published_at=NULL
```

In the hybrid variant, returning the job to `PENDING` and inserting the next due outbox hint happen in the same transaction. The publisher ignores `msg-19` until `available_at`; then the broker wakes a worker, which still must claim `job-55` from the database. In the DB-only variant there is no retry outbox row—the polling query discovers the job after `next_attempt_at`.

**Invariant**: only `token-a` can schedule the retry; the next claim respects `next_attempt_at`; attempt and wall-clock budgets are persisted; hybrid dispatch has durable evidence for the next wake-up.

**Crash before retry commit**: lease expiry lets reconciliation apply the same classification or recover provider evidence. **Crash after commit**: the durable timestamp controls the next attempt, and the publisher eventually discovers its matching outbox row.

**Recovery mechanism**: jittered backoff, a conditional `RUNNING → PENDING` transition, and either a due outbox hint or direct DB polling. Exhausted or permanent failures atomically set job and workflow to `FAILED` and create DLQ/operator evidence.

**Verification**: freeze database time and assert no claim or publish occurs before the due time, then assert the hint is delivered and only one worker claims attempt 2. Exhaust the attempt budget and assert no additional attempt starts after terminal failure.

See [Retries, Timeouts, and Cancellation](../reliability/04_retries_timeouts_and_cancellation.md).

---

## 12. Cancellation and completion deliberately race

A cancellation command uses expected workflow version and records actor, authorization basis, and reason. If it commits first:

```text
workflow_runs
run-42 | CANCELLED | version=10 | cancellation_requested_at=10:01:20Z

workflow_transitions
run-42 | version 10 | cancel | CANCELLED | user-9 | reason="withdraw publication"

jobs
job-55 | RUNNING | token-a | cancellation observed at next safe point
```

**Invariant**: exactly one of cancellation or completion wins version 10. A committed external effect is compensated by a new idempotent operation; cancellation does not pretend to erase it.

**Crash after cancellation**: durable state prevents new follow-up work even if no signal reaches the worker.

**Recovery mechanism**: worker safe-point checks and a compensation job when `provider_operations.state=SUCCEEDED`.

**Verification**: synchronize cancel and complete behind a barrier. Assert one update returns one row, the other zero, and any required compensation key is unique.

---

## 13. Reconciliation handles the crash no handler observed

Now take the hardest timeline:

```text
Worker A owns token-a
  → provider commits gen-884
  → A is killed before local result
  → heartbeat stops
  → lease expires
  → reconciler inspects job + provider operation
  → reconciler returns the job to PENDING + records a new delivery hint
  → Worker B receives the hint, or a DB poller finds the row
  → B claims with token-b
  → B retries with the same provider key
  → provider returns gen-884
  → result persists
  → conditional completion commits
```

Rows at discovery time:

```text
workflow_runs:      run-42 | GENERATION_RUNNING | version=9
jobs:               job-55 | RUNNING | token-a | lease expired
provider_operations: run-42:generate_final:v3 | INTENT | request_hash=sha256:6ac…
outbox:              msg-18 | published
```

**Invariant**: reconciliation never assumes `INTENT` means failure. It uses the stable key to ask the provider or safely repeat the same request.

**Crash during reconciliation**: every repair is conditional and idempotent; the next pass repeats it.

In the hybrid variant, an unacknowledged broker message may redeliver first, but the reconciler is the durable backstop: when it makes an expired job eligible again, it also records a fresh outbox hint. Duplicate hints remain harmless because only the conditional DB claim grants `token-b`.

**Recovery mechanism**: bounded expired-lease scan, durable redispatch or direct DB polling, token replacement, provider-result recovery, and terminal finalization.

**Verification**: pause A beyond lease expiry, let B recover and complete, then resume A. Assert one provider effect, one completion history row, and zero successful stale-token updates.

See [Reconciliation, DLQ, and Observability](../reliability/05_reconciliation_dlq_and_observability.md).

---

## 14. The acceptance test follows the same evidence chain

The system is ready when one integration suite proves these observable outcomes:

| Injection point | Required durable outcome |
|---|---|
| Before/after approval commit | All transition rows exist together or none do |
| After broker publish, before outbox mark | Duplicate delivery, one job claim |
| After claim, before heartbeat | Lease expires; new token replaces old token |
| Heartbeat failure during provider wait | Local work cancels; no stale completion |
| After provider effect, before result persist | Repeated request returns one provider operation |
| After provider result, before completion | Reconciler finalizes without another effect |
| Retryable provider failure | Durable jittered schedule emits a due hint and respects both budgets |
| Permanent/exhausted failure | Terminal rows plus inspected redrive evidence |
| Cancellation versus completion | One workflow version wins; compensation is explicit |
| Old worker returns after reclaim | Every old-token write affects zero rows |

**How you know it is working**: for any run, an operator can move from workflow transition to job attempt, attempt token, provider operation, outbox message, and final artifact using stable IDs. Queue depth alone is not this success signal.

Do not copy this full design for one best-effort email or one transaction-local calculation. Start with the smallest recovery contract in [When a Task Becomes a Workflow](../02_when_a_task_becomes_a_workflow.md), then add only the mechanisms whose failure timelines are real for the workload.

---

**Next**: [Part 4: Queue and Worker Architectures](../04_queue_and_worker_architectures.md)
