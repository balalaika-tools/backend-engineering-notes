# A Task Becomes a Workflow When Progress Has Business Meaning

> **Who this is for**: Backend engineers deciding whether background work needs durable business state or only a job record.

Before reading this, understand the responsibility split in **[Background Work Is Several Separate Systems](01_overview.md)**.

---

## 1️⃣ A restart must not erase a business decision

A worker finishes research, the system waits six hours for an editor, and generation begins only after approval. A chain of queue messages cannot answer what should happen after a restart, who rejected the draft, or whether generation is still legal. The progress is part of the business record, so it needs a durable workflow.

A **task** has one execution outcome. A **workflow** has a lifecycle whose intermediate decisions remain meaningful even when no code is running. A **state machine** defines which named event may move that lifecycle from one durable state to another.

```text
one task
  input ──► execute ──► result

workflow
  command ──► durable decision ──► work ──► durable decision
                    ▲                         │
                    └──── wait / retry ──────┘
```

> **The near-miss**: `PENDING`, `RUNNING`, and `DONE` look like a workflow because they form a sequence. They normally describe one job attempt. Workflow states express domain facts such as `WAITING_FOR_HUMAN_REVIEW`, `APPROVED`, or `CANCELLED`.

---

## 2️⃣ Six signals justify persistent workflow state

Use persistent workflow state when any **default trigger** below changes what the system is allowed to do next. The remaining signals strengthen the case but do not decide it alone.

| Signal | Why persistence is needed | Default trigger? |
|---|---|:---:|
| Multiple dependent business steps | Later work needs durable evidence that an earlier decision completed | ✓ |
| Minutes, hours, or days of elapsed time | No process can safely own the whole lifetime | ✓ |
| Human approval or external signal | Execution must stop without holding a worker | ✓ |
| Branching or cancellation | The chosen path must survive restart and races | ✓ |
| Parallel children with a join | The expected set and each completion must be synchronized durably | ✓ |
| Compensation | The system must know which committed effects need an inverse action | ✓ |
| Step-specific retry policy | Attempt history and next eligible time matter | |
| Audit history | The system must explain who changed what and why | |
| Process-restart continuity | Another worker must know where to resume | |
| Progress reporting | Users need more than one terminal job result | |

The test is not “does this have several functions?” It is: **after a crash, does the next process need an authoritative answer about which action is legal?** If yes, persist the workflow decision before executing that action.

> **Key insight**: A workflow state is a recovery decision point. It tells a future process what may happen next, not where the previous process happened to stop.

---

## 3️⃣ Slow or remote work can still be one task

Do not create business states merely because work is expensive, asynchronous, or runs on another machine.

| Usually one task | Why a workflow is unnecessary | Durable record still needed when loss matters? |
|---|---|:---:|
| Send one email | One replay-safe side effect, no later business decision | ✓ |
| Resize one image | One input maps to one output | ✓ |
| Refresh a cache | Current cache contents are the success signal | Sometimes |
| Deliver one webhook | Attempts matter, but there is no multi-step domain lifecycle | ✓ |
| Parse one document | A job can hold attempts and the result reference | ✓ |

A job record can store `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, attempts, errors, and a result reference. That is execution evidence, not a second domain model.

Use a task when a failed attempt can be discarded and the whole operation safely tried again. Escalate to a workflow when retrying from the beginning would repeat an irreversible decision, skip a required approval, or lose meaningful progress.

---

## 4️⃣ Model durable decisions, not code locations

The editorial example used throughout this section has this lifecycle:

```text
NEW
  └── queue_research ──► RESEARCH_QUEUED
                           └── claim_research ──► RESEARCH_RUNNING
                                                      └── research_succeeded
                                                            ▼
                                               WAITING_FOR_HUMAN_REVIEW
                                                 ├── reject ──► RESEARCH_QUEUED
                                                 └── approve ─► GENERATION_QUEUED
                                                                    └── ... ──► COMPLETED

Any non-terminal state ── cancel ──► CANCELLED
An exhausted required step ─────────► FAILED
```

Each state earns persistence because an API caller, worker, operator, or audit trail needs it after the process that wrote it has disappeared. `INSIDE_HANDLER`, `CALLING_PROVIDER`, and `LOOP_ITERATION_3` do not meet that test unless they correspond to a recoverable contract.

For every transition, name four things:

| Question | Editorial `approve` answer |
|---|---|
| Which event was requested? | `approve` |
| From which state is it legal? | `WAITING_FOR_HUMAN_REVIEW` |
| What evidence must exist? | Reviewer identity, decision reason, current workflow version |
| What durable work becomes owed? | One generation job or outbox event |

Callers submit `approve`; they do not submit `to_state=GENERATION_QUEUED`. Application code owns that mapping so no endpoint can invent an otherwise unreachable state.

---

## 5️⃣ Keep workflow, job, and delivery state separate

At one instant, the editorial run can truthfully have all three states:

```text
workflow_runs.state = WAITING_FOR_HUMAN_REVIEW
jobs.status         = FAILED                 # research attempt 2
queue delivery      = VISIBLE                # retry hint
```

The workflow row answers what the business process means. The job answers what happened to one requested execution. The queue answers whether a delivery is pending or in flight. None can replace the other without losing a failure boundary.

A practical low-to-medium-volume default is:

```text
domain database owns workflow state
  ├── optimistic compare-and-set protects transitions
  ├── job or outbox row records execution intent atomically
  └── queue workers execute the owed work
```

That default is implemented in [Database-Backed State Machines](state_machines/02_database_backed_state_machine.md). The three design decisions remain independent; the next note makes the alternatives explicit.

---

## 6️⃣ Version the definition as well as each run

`workflow_runs.version` serializes concurrent commands against one run. It does not say which transition graph created that run. Store both:

```text
workflow_definition_version = 3   # compatibility contract chosen at creation
version                     = 8   # optimistic-concurrency revision of this run
```

When deployment version 4 adds a new state, an in-flight version-3 run needs an explicit policy:

- **Pinned execution** — keep applying the version-3 graph until the run terminates. This is the safest default for incompatible changes.
- **Compatible migration** — atomically transform state and record a `workflow_migrated` transition.
- **Drain and replace** — stop creating old runs, let them finish, then remove the old handler.

⚠️ Changing the meaning of an existing state in place makes old rows ambiguous. A worker deployed after the change may apply new rules to a run created under old assumptions.

---

## 7️⃣ Know when the workflow boundary is wrong

**How you know it is working**: after stopping every worker and restarting the service, the API can show the current business state, the legal next events, and the evidence behind the last transition without consulting queue internals.

⚠️ If operators must inspect broker messages to decide whether a run is approved, queue state has become accidental business authority.

⚠️ If adding a state requires editing unrelated worker branches in several services, the transition model has no single owner.

Do not build a workflow for one replay-safe outcome; use a durable job. Do not build a custom database runtime when the product needs many long-lived timers, signals, compensation, deterministic replay, and frequent definition evolution; use a durable workflow engine. Do not use a checkpointed graph unless graph-shaped or agent state is central to the problem.

---

**Next**: [Minimal Durable Task Lifecycle](03_minimal_durable_task.md)
