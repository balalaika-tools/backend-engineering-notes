# background_work/
Suggested new notes: 6

GAP: State-machine modeling implementations in application code — compare explicit branching, a declarative transition registry or FSM library, state objects, and hierarchical statecharts by implementing the same named-event workflow and showing validation, guards, side-effect boundaries, testing, extension cost, and the point at which each model becomes unmanageable.
  SIGNAL: leaned-on
  EVIDENCE: `02_stateful_workflows_and_state_machines.md` §3 draws a transition graph and says each transition needs a named event and precondition, but §5 jumps directly to SQL for one transition; no note shows how application code owns and enforces the transition model or compares the alternative ways to express it.

GAP: Event-sourced state machines and process managers — derive current workflow state by folding immutable events, enforce optimistic stream versions, emit commands and follow-up events without duplicating effects, rebuild projections, evolve event schemas, and contrast this model with the current-state-row plus audit-history design.
  SIGNAL: leaned-on
  EVIDENCE: `02_stateful_workflows_and_state_machines.md` §4 stores an ordered `workflow_transitions` history and §7 warns against competing sources of truth, but the note never distinguishes an audit log from an authoritative event stream or explains when events rather than `workflow_runs.state` should own the lifecycle.

GAP: End-to-end database-backed state machine implementation — one complete workflow from API command through allowed-event validation, atomic transition/job creation, lease claim and heartbeat, idempotent side effect, retry, cancellation, terminal completion, and reconciliation.
  SIGNAL: leaned-on
  EVIDENCE: `02_stateful_workflows_and_state_machines.md` §3 requires "Worker owns a valid lease" for `RESEARCH_QUEUED → RESEARCH_RUNNING`, §4 defines lease columns without the ownership protocol, and §5 implements only one human-approval transition; `03_queue_and_worker_architectures.md` §2 implements a worker loop; `05_production_reliability_patterns.md` separately teaches lease, idempotency, retry, cancellation, and reconciliation. No file assembles these into the workflow the reader is expected to build, and the existing treatment is too fragmented to carry the central implementation promised by `background_work/README.md`'s "Persistent state machines, job records, transitions, and human checkpoints" scope.

GAP: Scheduling and periodic firing as a framework-neutral responsibility — cron/interval semantics, timezones and DST, misfire and catchup policy, overlap policy (skip/coalesce/serialize), and guaranteeing one durable firing across replicas.
  SIGNAL: leaned-on
  EVIDENCE: `background_work/README.md:3` promises to separate scheduling from delivery, execution, and workflow state; `01_overview.md` §2 marks **Scheduler** as one of six core responsibilities; `04_task_execution_models.md` §7 gives it only a short handoff and defers overlap to "the job contract" without teaching that contract. The mechanisms exist only inside framework-specific APScheduler, Celery, and Airflow notes.

GAP: Testing and failure-injecting a background-work system — executable crash/redelivery, lease-contention, lost-heartbeat, cancellation-race, outbox-gap, retry-exhaustion, and redrive tests.
  SIGNAL: leaned-on
  EVIDENCE: `06_decision_guide.md` §8 requires seven failure tests before production, including killing a worker after the external effect and racing cancellation against completion; `05_production_reliability_patterns.md` §2 requires a concurrent transition test; the only concrete tests in this folder are Dramatiq stub-broker examples, which do not implement the framework-neutral failure cases.

GAP: Durable fan-out and parallel join — bounding N child jobs, persisting the expected child set, recording each completion idempotently, and scheduling the aggregate exactly once when the last child lands.
  SIGNAL: leaned-on
  EVIDENCE: `02_stateful_workflows_and_state_machines.md` §1 lists parallel steps with a join as a persistence trigger; `04_task_execution_models.md` §1 says to "Bound fan-out and aggregate durably"; `05_production_reliability_patterns.md` §10 warns about runaway fan-out. No framework-neutral note teaches the durable join or its races.

# background_work/frameworks/
Suggested new notes: 2

GAP: Durable workflow engine (Temporal-class) — workflows versus activities, workers, durable timers, signals and queries, deterministic replay, workflow versioning, and idempotency at activity boundaries.
  SIGNAL: leaned-on
  EVIDENCE: `02_stateful_workflows_and_state_machines.md` §7, `03_queue_and_worker_architectures.md` §5, and `06_decision_guide.md` §4 all route timers, signals, replay, compensation, and frequent workflow evolution to a durable engine; `06_decision_guide.md` then sends readers to `frameworks/README.md` for implementation details, but that index contains no durable-engine note.

GAP: Checkpointed graph runtime (LangGraph) — checkpointers, persisted graph state, interrupts and resume, graph evolution, and keeping external side effects replay-safe across resumed execution.
  SIGNAL: leaned-on
  EVIDENCE: `02_stateful_workflows_and_state_machines.md` §6 cites LangGraph as the concrete human-checkpoint mechanism; `03_queue_and_worker_architectures.md` §1 and §5 make checkpointed graphs a first-class architecture; `06_decision_guide.md` §6 gives LangGraph its own framework row. No note in the repository teaches it.

# background_work/frameworks/celery/
Suggested new notes: 2

GAP: Celery Canvas — signatures, `chain`, `group`, `chord`, callbacks, the default primitive for each shape, and failure/retry semantics of a partially completed canvas.
  SIGNAL: leaned-on
  EVIDENCE: `frameworks/dramatiq/overview.md` makes Canvas primitives a named reason to choose Celery; `frameworks/celery/overview.md` §2 and §16 mention Canvas only to warn that it is not a domain state machine, without explaining any primitive the comparison asks the reader to choose.

GAP: Celery + FastAPI integration — app/worker process split, broker initialization and import order, outbox-backed submission, durable status instead of `AsyncResult`, testing the application task, and container/worker layout.
  SIGNAL: domain-expectation
  EVIDENCE: none — domain expectation. `frameworks/README.md` gives Dramatiq a dedicated FastAPI integration note while the decision guide treats Celery and Dramatiq as equivalent task-runtime entry points.

# background_work/frameworks/airflow/
Suggested new notes: 1

GAP: Asset-based data-aware scheduling — producing and consuming assets, triggering runs from asset events rather than time intervals, and the interaction with partitions, catchup, and backfills.
  SIGNAL: domain-expectation
  EVIDENCE: none — domain expectation. The folder scopes itself to scheduled data-workflow orchestration, but its scheduling examples are entirely cron/data-interval based.

# fundamentals/fastapi/
Suggested new notes: 1

GAP: `def` versus `async def` handlers and Starlette's AnyIO thread pool — what is offloaded, the shared capacity limiter, how slow synchronous work starves other handlers, and when to resize or bypass it.
  SIGNAL: leaned-on
  EVIDENCE: `fundamentals/fastapi/09_background_tasks_and_routers.md` says sync tasks run in the thread pool, while the streaming, middleware, and WebSocket notes also prescribe thread-pool offload; no FastAPI note explains the pool's ownership, capacity, starvation symptoms, or success signals.

# background_work/frameworks/dramatiq/
NO-GAPS: the overview and FastAPI integration name all Dramatiq concepts they rely on; the remaining problems are incorrect or incomplete implementations recorded in the per-file audit, not missing subject notes.

# background_work/frameworks/apscheduler/
NO-GAPS: the overview covers all APScheduler concepts it relies on, including triggers, timezone/DST, persistence, misfires, coalescing, overlap, single-scheduler deployment, locking, monitoring, and the 4.x boundary; its residual defects are per-file correctness issues.
