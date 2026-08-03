# An Event-Sourced State Machine Makes History Authoritative

> **Who this is for**: Engineers evaluating whether immutable domain events should own workflow state instead of a mutable current-state row.

Before reading this, understand the current-state-row model in **[Database-Backed State Machines](02_database_backed_state_machine.md)**.

---

## 1. An audit table cannot recover state unless it owns state

A team deletes a corrupted `workflow_runs` row and tries to rebuild it from `workflow_transitions`. Several rows contain free-form metadata, one deployment skipped a history insert, and the ordering contract was never defined. The table was useful evidence, but it was not an event store.

In an **event-sourced state machine**, the ordered event stream is the source of truth. Current state is obtained by folding those events; query tables are disposable projections.

```text
stream workflow-run-42
  1 WorkflowStarted
  2 ResearchQueued
  3 ResearchClaimed
  4 ResearchCompleted
  5 ReviewApproved
            │
            └── fold ──► GENERATION_QUEUED
```

An audit history describes changes made elsewhere. An authoritative stream must have strict per-stream order, optimistic append concurrency, stable event schemas, and enough information to rebuild every claimed projection.

> **The near-miss**: storing JSON events does not make a system event-sourced. The decisive question is whether deleting the current-state projection and replaying the stream is a supported recovery operation.

---

## 2. Folding events produces the decision state

This runnable model folds the editorial stream and rejects impossible histories:

```python
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class State(StrEnum):
    NONE = "NONE"
    NEW = "NEW"
    RESEARCH_QUEUED = "RESEARCH_QUEUED"
    RESEARCH_RUNNING = "RESEARCH_RUNNING"
    WAITING_FOR_HUMAN_REVIEW = "WAITING_FOR_HUMAN_REVIEW"
    GENERATION_QUEUED = "GENERATION_QUEUED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any]


def evolve(state: State, event: Event) -> State:
    transitions = {
        (State.NONE, "WorkflowStarted"): State.NEW,
        (State.NEW, "ResearchQueued"): State.RESEARCH_QUEUED,
        (State.RESEARCH_QUEUED, "ResearchClaimed"): State.RESEARCH_RUNNING,
        (State.RESEARCH_RUNNING, "ResearchCompleted"): State.WAITING_FOR_HUMAN_REVIEW,
        (State.WAITING_FOR_HUMAN_REVIEW, "ReviewApproved"): State.GENERATION_QUEUED,
    }
    if event.type == "WorkflowCancelled" and state not in {State.NONE, State.CANCELLED}:
        return State.CANCELLED
    try:
        return transitions[(state, event.type)]
    except KeyError as exc:
        raise ValueError(f"{event.type} is impossible after {state}") from exc


def fold(events: list[Event]) -> State:
    state = State.NONE
    for event in events:
        state = evolve(state, event)
    return state


if __name__ == "__main__":
    history = [
        Event("WorkflowStarted", {"definition_version": 3}),
        Event("ResearchQueued", {"operation_key": "run-42:research:v3"}),
        Event("ResearchClaimed", {"attempt_token": "token-a"}),
        Event("ResearchCompleted", {"result_ref": "s3://results/run-42/research.json"}),
        Event("ReviewApproved", {"reviewer_id": "editor-7", "reason": "verified"}),
    ]
    assert fold(history) is State.GENERATION_QUEUED
    print(fold(history))
```

The fold is pure: no current time, random UUID, network call, or database lookup. Any value needed later is captured in the event when the decision is made.

---

## 3. Expected stream version serializes commands

A minimal PostgreSQL event store can enforce one position per workflow stream:

```sql
CREATE TABLE workflow_events (
    stream_id UUID NOT NULL,
    stream_version BIGINT NOT NULL,
    event_id UUID NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    event_schema_version INTEGER NOT NULL,
    data JSONB NOT NULL,
    metadata JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (stream_id, stream_version)
);
```

For `approve`, the command handler loads events through stream version 4, folds them to `WAITING_FOR_HUMAN_REVIEW`, validates reviewer evidence, and attempts to append version 5:

```sql
INSERT INTO workflow_events (
    stream_id, stream_version, event_id,
    event_type, event_schema_version, data, metadata
)
VALUES (
    :run_id,
    :expected_stream_version + 1,
    :event_id,
    'ReviewApproved',
    1,
    jsonb_build_object(
        'reviewer_id', :reviewer_id,
        'reason', :reason,
        'generation_operation_key', :operation_key
    ),
    jsonb_build_object(
        'command_id', :command_id,
        'actor_type', 'user',
        'correlation_id', :correlation_id
    )
)
ON CONFLICT (stream_id, stream_version) DO NOTHING
RETURNING stream_version;
```

Zero rows mean another command appended first. Reload the new suffix, fold again, and re-decide. If `cancel` won, `approve` is now illegal; it is not a database retry.

> **Key insight**: Optimistic stream versioning serializes decisions, but replay safety comes from event contents and command idempotency. Ordering alone does not prevent a duplicated external effect.

---

## 4. Events and outgoing commands share one commit point

`ReviewApproved` means generation became owed. Publishing `GenerateFinal` directly after the append recreates the database/broker dual-write gap. Store the outgoing command in the same transaction:

```sql
CREATE TABLE command_outbox (
    command_id UUID PRIMARY KEY,
    stream_id UUID NOT NULL,
    caused_by_event_id UUID NOT NULL REFERENCES workflow_events(event_id),
    command_type TEXT NOT NULL,
    operation_key TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
```

The command service opens one transaction, appends `ReviewApproved`, and inserts `GenerateFinal` using the returned `event_id`. If the append loses its expected-version race, no command row is inserted. A publisher may deliver the command more than once, so the activity uses the stable `operation_key`.

When a worker succeeds, it sends a new command such as `RecordGenerationSucceeded`. The state-machine handler reloads/folds the stream and appends `GenerationCompleted`; the activity itself does not mutate the projection as an alternative source of truth.

---

## 5. Process managers turn events into follow-up work

A **process manager** subscribes to committed events and emits commands for the next step. Its own checkpoint and deduplication record must be durable:

```text
ReviewApproved(event_id=e5)
  └── process manager records e5 handled
        └── emits GenerateFinal(operation_key=run-42:generate:v3)
```

If both writes share one database, use an inbox plus outbox transaction. If they do not, the process manager may observe the same event again; a unique `operation_key` makes re-emission safe. Do not hide business branching in anonymous broker callbacks—name the resulting event and command so history remains explainable.

Timers follow the same model: an event records `ReviewDeadlineScheduled(deadline=...)`; a durable timer service later submits `ReviewTimedOut(command_id=...)`. The stream version decides whether that timeout is still legal when it arrives.

---

## 6. Projections are rebuildable and intentionally stale

API reads should not fold an unbounded stream on every request. Project events into query tables:

```sql
CREATE TABLE workflow_run_projection (
    id UUID PRIMARY KEY,
    state TEXT NOT NULL,
    last_stream_version BIGINT NOT NULL,
    reviewer_id TEXT,
    result_ref TEXT,
    updated_at TIMESTAMPTZ NOT NULL
);
```

The projector handles each event in order and updates `last_stream_version` conditionally. Replaying an already applied event is a no-op. A gap—receiving version 8 while the projection is at 6—stops that stream and fetches the missing suffix rather than applying out of order.

**How you know it is working**: drop a disposable projection, replay all streams, and compare the rebuilt rows and counts with the live projection. A mismatch identifies a non-deterministic fold, skipped event, or projector bug.

Snapshots may cache folded state at stream version N, but they are an optimization. Recovery must be able to load the snapshot and replay events N+1 onward; a stale snapshot cannot be authority.

---

## 7. Event schemas evolve by addition and interpretation

Never rewrite old events merely to match a new class definition. Record `event_schema_version` and use one of these policies:

- **Upcast on read** — transform older payloads into the current in-memory shape without changing storage.
- **Handle several versions** — keep explicit fold branches for old and new event schemas.
- **Emit a corrective/migration event** — record a new domain fact when business meaning changes.

Workflow definition version belongs in `WorkflowStarted`. New code either continues the old definition for that stream or appends an explicit migration event. Removing a field that an old fold requires breaks replay even if current projections still look correct.

⚠️ Side effects during replay duplicate payments, emails, and provider calls. Folds and projectors update local derived state only; external work stays behind idempotent commands.

⚠️ An event named `StateChanged` with arbitrary `from` and `to` values preserves chronology but loses domain meaning. Prefer `ReviewApproved`, whose schema contains the evidence that made the decision legal.

Do not choose event sourcing only to obtain an audit log. A current-state row plus append-only transition evidence is simpler when reads dominate, the latest state is the business record, and projections/replay would add no product value. Use event sourcing when the immutable sequence itself must be retained, reinterpreted, and projected in several ways.

---

**Next**: [End-to-End Database-Backed Workflow](04_end_to_end_workflow.md)
