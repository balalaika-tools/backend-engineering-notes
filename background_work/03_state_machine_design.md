# State-Machine Design Has Three Independent Axes

> **Who this is for**: Backend engineers who know a workflow needs durable state and now need to choose how to model, persist, and execute it.

Before reading this, decide whether the lifecycle is really a workflow in **[When a Task Becomes a Workflow](02_when_a_task_becomes_a_workflow.md)**.

---

## 1. “Use a state machine” leaves three decisions unresolved

Two teams can draw the same transition graph and build systems with completely different recovery behavior. One keeps state in a Python object and calls the provider inline; another uses optimistic database transitions and queue workers; a third lets a durable engine own history and activities. All three use a state machine, but only the latter two survive process loss.

Make these decisions separately:

```text
1. Describe legal transitions
   if/match | transition registry | State pattern | hierarchical statechart
                                  │
                                  ▼
2. Persist and synchronize state
   memory | DB + optimistic CAS | row lock/single writer | event stream | workflow engine
                                  │
                                  ▼
3. Execute the work created by a transition
   inline | queue workers | engine-managed activities
```

The editorial event remains the same in every comparison:

```text
event: approve
required current state: WAITING_FOR_HUMAN_REVIEW
required evidence: reviewer_id + reason + expected_version
derived target: GENERATION_QUEUED
owed work: generate_final job
```

> **The near-miss**: a transition library solves axis 1. It does not make state durable, serialize concurrent commands, create work atomically, or recover a worker after a crash.

---

## 2. Choose the simplest transition model that keeps one owner

All four models below can correctly reject `approve` from `NEW`. The difference is how clearly they express growth in guards, shared behavior, and nesting.

| Transition model | How `approve` is expressed | Extension cost | First failure mode | Default? |
|---|---|---|---|:---:|
| `if` / `match` | One branch checks state and event, then derives the target | Low while the graph is small | Rules scatter across handlers | For very small lifecycles |
| Transition registry/table | `(state, event)` maps to a guarded transition function | One entry and one focused test | Side effects leak into declarative metadata | ✓ for most service workflows |
| State pattern | State objects expose allowed event methods | New class or method per behavior | Class proliferation hides the graph | For state-specific behavior |
| Hierarchical statechart | Nested states inherit transitions such as `cancel` | Efficient when many substates share behavior | Entry/exit semantics become unfamiliar | For genuinely nested or concurrent regions |

The default registry keeps the graph inspectable while application functions still own guards and emitted commands. The deep dive implements all four against the same event: [Application-Code Approaches](state_machines/01_application_code_approaches.md).

Do not select a statechart merely because the diagram looks sophisticated. Select it when flattening `ACTIVE.RESEARCH.*` and `ACTIVE.GENERATION.*` duplicates the same cancellation, timeout, or authorization rule across many states.

---

## 3. Persistence determines recovery and concurrency

This axis decides where the authoritative lifecycle lives. Compare each option using the same operational questions rather than product features.

| Persistence model | Source of truth | Atomicity and concurrency | Crash recovery | Timers/signals | Definition versioning | Observability / cost |
|---|---|---|---|---|---|---|
| In-memory object | One process heap | Local lock only | State disappears | Process-local only | Deploy replaces code | Easy locally; no durable operations |
| **DB row + optimistic CAS** | **Current-state row plus history** | **`state + version` conditional update; job/outbox in same transaction** | **Reload row; reconcile owed work** | Scheduler creates durable commands | Store definition version; migrate explicitly | **SQL-friendly; team owns runtime** |
| Row lock or single writer | Database row or serialized command log | Transactional lock or one ordered consumer | Retry command after owner loss | External scheduler/queue | Same policy as CAS | Simpler conflicts; lower parallelism |
| Event stream | Immutable per-run events | Append with expected stream version | Fold events and resume commands | Timer service or process manager | Upcasters/new event types | Complete history; projection and event-evolution cost |
| Durable workflow engine | Engine history | Engine-defined deterministic command model | Replay history; retry activities | First-class durable timers/signals | Engine-specific patching/versioning | Rich execution view; another platform |

For the editorial workflow, **database row + optimistic compare-and-set is the default in this repository**: the domain database already owns approval, volume is moderate, and state transition plus job creation must commit together.

Choose row locking when one command must validate a short multi-row invariant that cannot be expressed as a conditional statement. Choose a single writer when strict per-run ordering is worth partitioning and head-of-line blocking. Choose an event stream when the immutable event sequence is the domain record, not merely an audit trail. Choose an engine when hand-building timers, signals, replay, and version evolution has become the product.

> **Key insight**: Transition syntax controls code complexity; persistence controls correctness after crashes. Never use a nicer transition API as evidence that the workflow is durable.

---

## 4. Execution determines how owed work reaches compute

A successful `approve` transition should return commands such as `ScheduleGeneration(run_id, operation_key)`. It should not make a ten-minute provider call while holding the transition transaction.

| Execution model | Atomic boundary after `approve` | Crash after state commit | Concurrency control | When it fits |
|---|---|---|---|---|
| Inline | Transition and local call cannot share a safe external transaction | Reconciler must discover missing effect; request may time out | Request/process limits | Tiny reversible local work only |
| **Queue workers** | **Transition creates job/outbox atomically; worker claims later** | **Durable intent remains and is reclaimed** | **Lease, queue visibility, worker/global limits** | **Default for database-backed workflows** |
| Engine activities | Workflow decision enters engine history; activity is scheduled by engine | Engine retries activity from history | Engine worker/task-queue limits | Engine owns workflow coordination |

Inline work is acceptable when it is a short database-only effect inside the same transaction. External I/O belongs after commit because no relational transaction can roll back a provider call.

---

## 5. Sensible combinations are fewer than the Cartesian product

The axes are independent, but not every combination is useful.

| Situation | Transition model | Persistence | Execution | Why |
|---|---|---|---|---|
| Small request-local form wizard | `match` | Memory/session | Inline | Loss is acceptable or the client resubmits |
| **Editorial backend workflow** | **Registry** | **DB + optimistic CAS** | **Queue workers** | **One inspectable graph, atomic business writes, recoverable execution** |
| Contended account aggregate | Registry | Row lock/single writer | Queue workers | Multi-row invariant or strict ordering dominates |
| Audit-authoritative domain | Pure decision function | Event stream | Process-manager commands | Events, not current rows, are the source of truth |
| Nested device lifecycle | Hierarchical statechart | DB CAS or event stream | Queue workers | Shared parent behavior justifies hierarchy |
| Long-lived signal/timer workflow | Engine workflow code | Engine history | Activities | Runtime owns replay and durable waits |

⚠️ In-memory state plus durable queue delivery produces orphan messages after restart: the worker receives work whose precondition vanished.

⚠️ Database state plus an unconditional broker publish creates a dual-write gap. Put a job or outbox row in the same transaction as the transition.

⚠️ Engine history plus a second database state machine creates two authorities. Let the engine own coordination and the database own domain entities, with an explicit boundary.

---

## 6. Compare implementations with one fixed review checklist

When evaluating any state-machine approach, answer these questions for the same `approve` event:

1. **Source of truth** — which durable record decides the current state?
2. **Legal transition** — where is `WAITING_FOR_HUMAN_REVIEW + approve` mapped to its only target?
3. **Atomicity** — how are reviewer evidence, the transition, and generation intent committed together?
4. **Crash recovery** — what remains if the process dies after each durable write?
5. **Concurrency** — what happens when `approve` races `cancel`?
6. **Timers and signals** — who owns the wait and delivery?
7. **Versioning** — how does an old run execute after the graph changes?
8. **Observability** — can an operator reconstruct the command, attempt, and result?
9. **Operational cost** — which polling, storage, runtime, and on-call duties were added?
10. **Boundary** — what is the first failure mode, and when should this option not be used?

**How you know the choice is coherent**: each answer names one owner. If “database or queue depending on timing” appears, the design has an unresolved split-brain boundary.

Do not optimize the modeling axis before deciding the source of truth. Do not add queue workers when inline database work already meets the recovery contract. Do not adopt an engine when durable timers and signals are hypothetical rather than required.

---

**Next**: [State-Machine Implementations](state_machines/README.md)
