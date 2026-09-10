# Background Work

> A failure-first path from one durable task to stateful workflows, replay-safe execution, and production operations.

The framework-neutral notes define the guarantees first. Framework notes then show which responsibilities particular runtimes own; they do not replace the business invariants in the reliability track.

---

## Contents

| Section | What it owns | Reader outcome |
|---|---|---|
| [Foundations](foundations/README.md) | Responsibility boundaries, the task/workflow threshold, and one minimal durable task | Build the smallest recoverable background-work system before choosing infrastructure |
| [Execution](execution/README.md) | Queue architecture, worker concurrency, and durable scheduling | Choose how work is delivered, executed, and triggered |
| [Stateful Workflows](workflows/README.md) | State-machine design, persistence models, fan-out/join, and reference workflows | Model durable business progress and assemble multi-step recovery |
| [Reliability](reliability/README.md) | Atomic intent, fencing, idempotency, retry/cancellation, reconciliation, and failure testing | Close each crash window with durable evidence and verify recovery |
| [Operations](operations/README.md) | Authorization, tenant fairness, capacity, and autoscaling | Operate shared worker fleets without losing isolation or boundedness |
| [Frameworks and Selection](frameworks/README.md) | System selection, orchestrator selection, and runtime-specific guides | Choose the smallest implementation that meets the recovery contract |

---

## Reading Order

### First durable task

**For**: API engineers meeting background work for the first time.

**Working result by entry 2**: submit, claim, retry, and look up one durable task, then explain which state belongs to the application rather than the delivery mechanism.

1. **Do:** [Minimal Durable Task](foundations/03_minimal_durable_task.md) — build the complete `PENDING → RUNNING → terminal` baseline.
2. **Understand:** [Responsibility Model](foundations/01_overview.md) — separate the business promise from delivery and execution around that result.
3. **Escalate only when needed:** [Task or Workflow?](foundations/02_task_or_workflow.md).
4. **Decide:** [System Selection Guide](frameworks/00_system_selection.md) — validate the smallest runtime that meets the recovery contract.

**Stop here if** one independent, replay-safe task and a result lookup endpoint meet the product need. Continue to [Execution](execution/README.md) for alternative delivery or compute models, [Stateful Workflows](workflows/README.md) when intermediate business progress becomes durable, or [Reliability](reliability/README.md) when external effects and process loss create recovery obligations.

### Database-backed workflow

**For**: engineers implementing a multi-step application-owned workflow.

**Working result by entry 2**: choose the three state-machine axes and atomically persist one named transition with the work it creates.

1. **Decide:** [State-Machine Design](workflows/01_state_machine_design.md).
2. **Build:** [State-Machine Modeling and Persistence](workflows/state_machines/README.md), using relational current state as the default.
3. **Choose execution:** [Queue and Worker Architectures](execution/01_queue_and_worker_architectures.md) and [Task Execution Models](execution/02_task_execution_models.md).
4. **Harden:** follow the [Reliability](reliability/README.md) path.
5. **Assemble:** trace the [Database-Backed Editorial Workflow](workflows/reference_workflows/01_database_backed_editorial_workflow.md).
6. **Verify and operate:** run [Failure Injection and Testing](reliability/06_failure_injection_and_testing.md), then continue to [Operations](operations/README.md).

**Stop here if** a few stable states, durable jobs, and explicit reconciliation meet the product need. Continue to [Frameworks and Selection](frameworks/README.md) when timers, signals, human waits, parallel branches, or workflow migrations are becoming a custom runtime.

### Choose a runtime

- **Choose the overall system:** start with the [System Selection Guide](frameworks/00_system_selection.md).
- **Evaluate workflow engines:** read [State-Machine Design](workflows/01_state_machine_design.md), then [Workflow Orchestrator Selection](frameworks/01_workflow_orchestrator_selection.md).
- **Inspect a specific runtime:** follow the relevant tool path in [Frameworks](frameworks/README.md).

---

## Related Sections

- [Long-running task patterns](../architecture/long_running_tasks/README.md) — client delivery, callbacks, task tokens, and infrastructure-specific examples
- [Concurrency fundamentals](../fundamentals/concurrency/README.md) — event loops, threads, processes, and synchronization
- [API idempotency](../fundamentals/fastapi/safe_and_scalable_api_calls/11_idempotency.md) — stable request keys and replay-safe API semantics
- [Distributed admission control](../fundamentals/fastapi/safe_and_scalable_api_calls/09_distributed_admission_control.md) — Redis-backed request, tenant, provider, and global limit mechanics
- [Redis rate limiting](../infrastructure/redis/05_rate_limiting.md) — fixed-window, sliding-window, and token-bucket primitives

---

## Prerequisites

- Comfortable reading Python and PostgreSQL
- Basic familiarity with transactions, HTTP clients, and containerized services
- No prior knowledge of a task queue or workflow framework is assumed
