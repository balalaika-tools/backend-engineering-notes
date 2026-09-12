# Trace One Investigation Across an API and a Worker

> **Who this is for**: Engineers who understand a port and an application action but want to read a real multi-service implementation from request to result.

## 1. Follow one accepted request before opening every folder

A client submits exception EX-1 and receives HTTP `202`. The investigation has been accepted,
but no analysis has necessarily happened. Finding the route alone cannot explain the result:
acceptance, publication, execution, and result lookup belong to different actions.

This worked trace uses the supplied `orchestrator` and `worker` samples. R-1, I-1, and E-1 are
readable stand-ins for UUIDs, not literal request values. Assume valid authentication and an
exception with no active investigation. This first pass follows successful execution:

```text
POST /v1/agent/investigate with {"exception_ids": ["EX-1"]}
  → RequestInvestigations commits request R-1, queued investigation I-1,
    request membership, and pending event E-1
  → API returns 202 with R-1, I-1, and a status URL
  → PublishOutbox sends E-1 and records broker confirmation
  → worker claims I-1 and runs InvestigateException
  → analysis and write-back outcomes are checkpointed; I-1 becomes completed
  → worker acknowledges its delivery; status/report actions expose persisted results
```

A **checkpoint** records completed progress durably so another attempt can resume it. An
**acknowledgement** tells the broker that this delivery is handled; it is separate from the
database's investigation state. The observable business result is I-1's persisted completion and
report, not merely the HTTP response or an empty queue.

**Stop after sections 1–3** if your goal is to navigate acceptance and its transaction. Continue
through sections 4–7 to understand duplicate delivery, crashes, and the evidence needed to trust
recovery. These are reading traces, not commands for starting the supplied deployment.

Source paths below are relative to `temp/services/<service>/src/<service>/`; test paths are
relative to the corresponding service root. They identify the supplied sample, while the traces
remain readable without that temporary directory. This is an architectural case study, not a
certification of the sample's correctness.

---

## 2. The route translates HTTP into the acceptance action

Start with orchestrator `api/routers/investigations.py`, then follow
`api/dependencies.py` into `bootstrap/runtime.py` once to see how the action was supplied.
Afterward, return to `application/request_investigations.py` to read the business operation.

The route chooses the explicit-ID action or the filtered-selection action according to the
request shape. It obtains the authenticated client identity from request state, supplies tracing
context, and converts the action's result into HTTP response models. Stable action errors become
API errors. Neither model invocation nor SQL belongs to that translation.

For the explicit-ID branch, follow these values:

```text
HTTP body exception_ids=[" EX-1 ", "EX-1"]
  → action validates batch size and nonempty identifiers
  → action strips whitespace and preserves one EX-1
  → result contains one investigation item
  → response selected_count=1
```

The sample checks the submitted batch length before deduplicating. A batch over the configured
limit is rejected even if all its entries would collapse to one ID. This ordering is business
behavior worth testing; knowing that validation belongs in `application/` is not enough to
predict it.

The `RequestedInvestigation` and `InvestigationBatch` dataclasses describe the action's result.
HTTP response schemas describe its transport representation. Separate types let a response field
such as `status_url` change without becoming part of database or model-provider logic.

> **Core:** read input values, action decisions, and output values together. Directory names locate
> an owner; the transformations explain its behavior.

---

## 3. Acceptance commits an obligation to publish

If the API commits I-1 and crashes before sending its message, work can disappear between the
database and broker. The sample writes an **outbox event**, a durable pending publication, inside
the same transaction as the investigation. A separate action sends it later.

The ownership chain is:

```text
RequestInvestigations
  → ports/investigation_store.py: InvestigationUnitOfWork
  → db/unit_of_work.py: SqlAlchemyInvestigationUnitOfWork
  → db/repositories/: request, investigation, and outbox operations
  → shared platform_db table models
```

The concrete Unit of Work gives those repositories one session. The action calls `commit()` only
after it has recorded the request's ordered membership. Read
[the Unit of Work explanation](05_design_ports_and_adapter_contracts.md#6-a-unit-of-work-makes-several-repositories-one-transaction)
for session lifetime, `flush()` versus commit, and rollback behavior.

Two superficially similar requests can produce different durable changes:

| Starting state for EX-1 | Result of `insert_or_attach` | New publication |
|---|---|---|
| No active investigation | Create I-1, `created=True` | Add E-1 to the outbox |
| I-1 is already queued or processing | Attach to I-1, `created=False` | No additional event |
| Previous investigation is terminal; explicit request permits rerun | Create I-2, `created=True` | Add E-2 |

The database's partial unique index permits only one active investigation per `exception_id`.
The repository handles an insert conflict by looking up the active row; the application uses
`created` to decide whether it owes a new event. Request membership allows distinct API requests
to refer to the same investigation.

This is active-work deduplication, not permanent HTTP request idempotency. A repeat after completion
can intentionally create another run. The filtered-selection action has additional rules for
excluding completed work; do not extrapolate the explicit branch's behavior to every endpoint.

> **Production:** the sample's active uniqueness key is `exception_id` alone. Applying this design
> to a multi-tenant system requires checking identifier scope and authorization; passing a
> `client_id` does not itself prove that cross-client attachment is permitted.

---

## 4. Publication confirmation still leaves a replay window

In orchestrator `application/publish_outbox.py`, `PublishOutbox` claims pending events, calls the
`EventPublisher` port, records success or failure, then commits. The NATS publisher supplies the
broker implementation; the outbox repository supplies database locking and timestamps.

An event becomes published in the database only after the broker confirms it. That ordering
prevents a premature “sent” record, but it cannot make two systems one transaction:

```text
database: E-1 pending
publisher: sends E-1; broker confirms acceptance
process: crashes before mark_published is committed
database: E-1 still pending
next publisher pass: sends E-1 again using the same event ID
```

The worker must tolerate delivery more than once. A stable publication ID helps broker
deduplication where configured, but its retention window is not a lifetime guarantee that the
business effect runs once. The canonical mechanism is
[atomic transitions and outbox](../../background_work/reliability/01_atomic_transitions_and_outbox.md).

The sample locks a pending batch with `FOR UPDATE SKIP LOCKED`, allowing another publisher to
skip rows already locked, and holds the transaction while publishing that batch. A slow broker
therefore extends database lock and connection occupancy. Batch size and publication timeouts
matter operationally even though the package placement is clean.

⚠️ A growing unpublished backlog with successful API responses indicates an acceptance-to-delivery
problem. Inspect pending-event age and publisher failures before investigating model prompts.

---

## 5. Delivery ownership and business progress are different states

Read worker `adapters/nats/handler.py` around `_handle()`, `_run_owned()`, and `_heartbeat()`.
The handler attempts to claim I-1 through `InvestigationDeliveryState`. A **lease** is temporary
database ownership with an expiry time so a crashed worker does not own the job forever.

After a successful claim, the handler runs the action and heartbeat concurrently. The heartbeat
both reports broker progress and extends database ownership. Those protect different systems:
the broker's delivery timer and the application's right to update the investigation.

| Observation | Sample handler behavior |
|---|---|
| Action completes normally | Acknowledge the delivery |
| Unclaimed investigation is already terminal | Acknowledge without running the action |
| Another live lease owns it | Request redelivery after the remaining lease duration |
| Ownership is lost while running | Cancel owned execution and request redelivery |
| Action raises a classified failure | Persist a decision through `RecordInvestigationFailure`, then retry or terminate delivery |

`RecordInvestigationFailure` owns the durable decision about another attempt or terminal failure.
The handler translates the committed outcome into NATS acknowledgement operations. Its local
protocols describe collaborators; they do not mean the application imports NATS types.

There is still an architectural tension: this handler also coordinates durable claims and examines
business statuses. For a second inbound transport, an application-level execution coordinator
could own claim/run/failure sequencing, leaving each adapter to translate delivery behavior. That
would improve reuse when it is needed; moving the existing file solely to match a tree would not
prove the boundary is better.

> **Production:** inspect ownership predicates before claiming stale writers are fenced out.
> **Fencing** means rejecting writes from an obsolete ownership attempt. The sample uses a
> process-level `worker_id` in many update predicates; it is not a fresh token per claim. If the
> same identity reclaims work while an old attempt survives, identity equality alone cannot
> distinguish them. The stronger attempt-token mechanism belongs to
> [leases, heartbeats, and fencing](../../background_work/reliability/02_leases_heartbeats_and_fencing.md).

---

## 6. Checkpoints explain what the worker repeats after a crash

Read `application/investigate_exception.py` through `execute()` first. On a fresh run it loads
owned state, resolves configuration context, fetches source data, obtains analysis, writes codes,
writes a comment, and marks completion. The private methods implement those named stages.

`ExceptionSource`, `ExceptionAnalyst`, `ReportStore`, and `CtcWriteBack` are the capabilities it
needs. The CTC integration is the sample's external exception system. Bootstrap chooses its
database reader, GenAI analyst, object store, and HTTP writer implementations. Follow their
constructors only after understanding the action's sequence.

The platform database session is closed during external work. After generating validated
analysis, the action stores the report and commits an analysis checkpoint containing the source
exception version. Later short transactions record write-back outcomes and completion.

An illustrative retry shows the consequence:

```text
attempt 1:
  analysis generated and checkpointed
  codes written and checkpointed
  comment call fails transiently
  failure recorder makes work eligible for retry

attempt 2:
  reload state; resolve context and fetch source data
  restore checkpointed analysis instead of another model invocation
  skip codes because their outcome is already complete
  retry comment; checkpoint outcome; mark investigation complete
```

The saved source version matters: using a newly fetched version with old analysis could authorize
an update based on evidence that no longer matches the source. The sample preserves the version
associated with the analysis. Its code-write adapter maps a version conflict to an explicit
skipped outcome; completion can therefore include skipped writes. “Completed” does not necessarily
mean every remote field was changed.

### A checkpoint cannot close an unrecorded external-effect window

Now move the crash earlier:

```text
external system accepts comment
worker crashes before recording comment outcome
retry sees no completed comment checkpoint
retry sends the comment again
```

The visible comment request does not supply a stable operation key. The sample alone therefore
does not establish that duplicate comments are prevented. That requires a provider idempotency
contract, a lookup that resolves the previous outcome, or another explicit recovery design.
See [idempotency and external effects](../../background_work/reliability/03_idempotency_and_external_effects.md).

Likewise, storing a report before its database checkpoint creates a separate failure window.
An ownership check on a later database write cannot roll back a completed upload or HTTP call.

⚠️ “There are checkpoints” is not enough evidence for safe retries. Identify the crash window
between each external effect and its durable local acknowledgement, then find the mechanism that
handles that window.

---

## 7. Tests turn the source tour into evidence

Start with the **acceptance** and **resume** rows below. They establish the main behavior before
the remaining tests examine concurrency and delivery. These are existing source locations to
inspect, not a claim that their suites have been executed as part of this walkthrough.

| Question | Sample test file, relative to its service root | What it can establish |
|---|---|---|
| **What does acceptance create?** | orchestrator `tests/unit/application/test_request_investigations.py` | Deduplication and action results with a fake Unit of Work |
| **Where does a retry resume?** | worker `tests/unit/application/test_investigate_exception.py` | Reuse of analysis and skipping a completed code-write step |
| Does real concurrent insertion attach correctly? | orchestrator `tests/integration/db/test_investigation_repositories.py` | Database constraint and conflict behavior |
| Does acknowledgement follow action completion? | worker `tests/unit/adapters/nats/test_handler.py` | Delivery ordering and failure translation with fakes |
| What if runtime composition fails? | worker `tests/unit/bootstrap/test_runtime.py` | Cleanup after resources were acquired |

The acceptance fake uses an in-memory lock and a no-op commit. Its passing concurrency test does
not prove PostgreSQL locking or rollback. Similarly, an action fake returning successfully cannot
prove an actual database commit. Use each test at the scope it really exercises, as explained in
[boundary testing](09_test_through_architectural_boundaries.md).

For periodic recovery, read `application/reconcile.py` with the max-delivery adapter and supervisor.
**Reconciliation** compares durable facts to expected progress. This sample counts stale work,
reports outbox age, applies decisions for supplied exhausted-delivery investigations, and deletes
expired history. Counting stale rows does not itself requeue them; inspect the actual mutation
and delivery path before assuming every stalled job is repaired.

**Success signal:** you can predict which rows change for a new request versus attachment, which
steps run after a checkpointed failure, and which crash window remains unresolved. If you can
name the folders but cannot predict those outcomes, return to the relevant trace rather than
memorizing the tree.

Do not copy this whole topology for a small synchronous operation. Use
[the minimal vertical slice](02_build_one_vertical_slice.md) until durable handoff, long execution,
or separate scaling creates the need for these additional owners.

> **Key insight**: understand a service by following the durable facts it creates and the contracts
> that move them forward; folders organize that story but do not supply its guarantees.

---

**Next**: [Part 13 — Share Libraries Without Duplicating Service Layers](13_share_libraries_without_service_layers.md).
