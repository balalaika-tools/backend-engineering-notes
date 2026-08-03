# Capacity Is Bounded by the First Shared Dependency

> **Who this is for**: Engineers sizing and autoscaling durable worker fleets against queue-age SLOs, resource limits, downstream quotas, and cost budgets.

Before reading this, understand workload-specific concurrency in **[Task Execution Models](../05_task_execution_models.md)** and shared budgets in **[Multitenancy, Admission, and Fairness](02_multitenancy_admission_and_fairness.md)**.

---

## 1. A growing queue can coexist with idle CPU

A fleet doubles from 10 to 20 pods, but report jobs still start late. CPU remains at 35%, so autoscaling adds more pods. Each pod opens 16 database connections, all workers contend for a provider account limited to 80 concurrent calls, pool wait rises, retries multiply, and queue age gets worse.

Worker replicas are not capacity by themselves. Capacity is the sustainable completion rate after the tightest shared constraint—CPU, memory, database connections, sockets, provider quota, serialized partitions, or cost—has been applied.

```text
arrival rate ──► durable backlog ──► worker slots ──► database/provider
                      │                   │                  │
                      │                   └── local ceiling  │
                      └── queue-age SLO                      └── shared ceiling

sustainable throughput = minimum throughput across every required stage
```

> **The near-miss**: queue depth looks like an autoscaling signal because more rows suggest more workers. Depth has no time dimension and no downstream context. Ten thousand one-second jobs and ten thousand ten-minute jobs are different incidents; adding pods past a provider or database ceiling only adds contention.

---

## 2. Size one workload class from demand and service time

Do not average email, PDF rendering, and provider calls into one number. For each independently scaled queue or stage, record:

```text
λ  = sustainable arrival rate in jobs/second
S  = measured service demand in seconds/job at a chosen percentile
C  = effective concurrent execution slots
μ  = sustainable service rate ≈ C / S jobs/second
Wq = allowed queue wait before work starts
```

Stability requires `μ > λ` over the planning window. Little's Law, `L = λW`, relates average in-system work, throughput, and elapsed time once the system is stable; it does not promise stability during an unbounded burst or hide percentile tails.

Consider 400 CPU-heavy reports arriving together at 03:00. Measured service demand is 30 CPU-seconds per report. One pod runs four process workers:

```text
one-pod service rate = 4 / 30 = 0.133 reports/second ≈ 8 reports/minute
one-pod drain time   = 400 / 8 = 50 minutes

required average slots for a 15-minute drain target
  = 400 reports × 30 seconds / 900 seconds
  = 13.3 slots

with 30% operational headroom
  = ceil(13.3 × 1.30)
  = 18 slots → five four-slot pods
```

The calculation produces a candidate, not permission to deploy it. Five pods must still fit memory, database, provider, and cost ceilings. Use percentile service time and a measured arrival envelope; a global daily average erases the burst the workers must actually drain.

> **Key insight**: scaling is a constrained budget allocation, not a replica-count reaction. The correct target is the smallest fleet that meets queue-age and throughput SLOs without crossing the first shared dependency ceiling.

---

## 3. The maximum useful concurrency is the minimum ceiling

Convert every bottleneck into the same unit: safe concurrent slots for this workload class.

```text
C_useful ≤ min(
    C_cpu,
    C_memory,
    C_database,
    C_http_or_socket,
    C_provider,
    C_serialized_partition,
    C_cost
)
```

The default review starts with **CPU/service time**, **memory per active job**, **database pool budget**, **provider concurrency/rate**, and **maximum replicas**.

| Ceiling | Derivation | Failure signal |
|---|---|---|
| **CPU/service time** | Cores or benchmarked native capacity per pod | CPU throttling, longer service time, scheduler run queue |
| **Memory per active job** | `(pod memory - runtime baseline) / high-water per job` | OOM kill, eviction, swap, rising high-water mark |
| **Database pool budget** | Connections reserved for workers / connections needed per active slot | Pool wait, connection refusal, database saturation |
| **Provider concurrency/rate** | Contracted global permits divided across workload classes | `429`, permit wait, reduced provider throughput |
| **Maximum replicas** | Deployment ceiling × safe slots per pod | Pending pods, quota exhaustion, pool multiplication |
| Socket/file descriptor budget | Process limit minus baseline and safety margin | Connect errors, descriptor exhaustion |
| Cost budget | Allowed spend per interval / expected cost per operation | Budget rejection, spend anomaly |
| Ordered partition count | Number of independent keys that may progress in parallel | Idle workers while hot partitions queue |

Suppose each report process peaks at 600 MiB and the runtime baseline is 500 MiB. Four active reports need roughly 2.9 GiB before headroom, so a 3 GiB pod is already unsafe even though four CPU cores exist. If five pods would open 80 database connections but workers have a 40-connection allocation, the database ceiling reduces the plan to two connections per pod or fewer active slots—not more replicas.

Size connection pools from `maximum replicas × pool size`, including API, publisher, scheduler, and reconciliation processes. A pool size that is safe on one pod becomes an outage when multiplied by autoscaling.

---

## 4. Bursts, retries, and fan-out amplify offered load

Capacity plans based only on successful first attempts are optimistic. For each stage, estimate an amplification factor:

```text
execution demand
  = admitted logical units
  × average attempts per unit
  × average per-unit service demand

fan-out demand
  = admitted parent operations
  × admitted children per parent
  × average attempts per child
```

Use a normal factor and an incident factor. A healthy provider may average 1.02 attempts per operation; a brownout can push the bounded factor toward the configured retry limit. The retry policy must fit the incident capacity budget, not merely the happy-path mean.

Scheduled work creates a known burst. Catch-up after downtime may create several missed periods at once. Apply the schedule's skip/coalesce/catch-up policy before sizing workers; “run every missed occurrence immediately” can exceed the original peak by an order of magnitude.

Fan-out pages, retry jitter, admission caps, and global provider permits keep amplification bounded. If a workload cannot name its maximum child count, attempt budget, and retained-result bytes, its capacity requirement is not yet finite.

---

## 5. Autoscaling follows age and saturation, then stops at a ceiling

Use oldest-ready age as the primary user-facing pressure signal and queue depth as supporting evidence. Add the saturation signal for the resource the next replica will consume.

| Observation | Interpretation | Scaling action |
|---|---|---|
| Oldest age rises, depth rises, dependency has headroom | Demand exceeds current worker service rate | Scale toward calculated slot target |
| Depth rises, oldest age stays low | Burst is being drained within SLO | Hold or scale conservatively |
| Oldest age rises, provider permits are full | Downstream is the ceiling | Stop scaling; defer admission or renegotiate quota |
| Oldest age rises, database pool wait rises | Workers are contending for the database | Stop scaling; reduce per-pod concurrency or database demand |
| CPU high, service time stable, downstream healthy | CPU class needs more process capacity | Scale CPU workers within memory and DB ceilings |
| CPU low, event-loop/pool wait high | I/O dependency or blocking path is limiting | Fix/bound dependency; replicas may worsen contention |

The first, third, fourth, and fifth rows are the default scaling decisions. Feed autoscaling a bounded target; never allow `replicas × per-pod concurrency` to exceed global database or provider permits. Scale down slowly enough to drain owned work, stop new claims first, and keep heartbeats alive as described in [Reconciliation and Operations](../reliability/05_reconciliation_dlq_and_observability.md#8-deployments-drain-ownership-before-processes-exit).

**How you know autoscaling is working**: a repeatable burst raises replicas or slots, oldest-ready age peaks below the SLO, then both backlog and replicas return to baseline without elevated `429`, pool-wait, lease-expiry, or OOM signals. The common silent failure is healthy aggregate throughput with one tenant or class starving; keep per-tenant/class age on the same dashboard.

---

## 6. Load tests reproduce the bottleneck, not just request volume

The production capacity suite uses the same payload sizes, connection pools, worker execution model, and downstream latency distribution as the real path. A fast no-op task measures broker overhead, not report capacity.

Run these **default scenarios** first: steady-state target, planned burst, slow dependency, and maximum replicas.

| Scenario | Injection | Required result |
|---|---|---|
| **Steady-state target** | Arrival rate at planned normal peak | Stable depth/age and resource utilization with headroom |
| **Planned burst** | Known schedule/fan-out envelope | Drain within queue-age SLO without violating limits |
| **Slow dependency** | Provider/database latency at incident percentile | Admission and concurrency reduce; no retry storm |
| **Maximum replicas** | Force autoscaler/deployment to configured ceiling | Connections, sockets, provider permits, and cost remain bounded |
| Retry amplification | Return bounded `429`/`503` mix | Jittered retries fit attempt and wall-clock budgets |
| Large-item tail | Inject high-memory/long-duration items | No OOM; short-class progress remains within SLO |
| Scale-down drain | Remove replicas with active attempts | Claims stop, heartbeats continue, no stale completion wins |

Record arrival/completion rate, oldest-ready age, per-stage service time, active slots, CPU, memory high-water, pool wait, provider permit wait, retries, and cost. Preserve the workload seed and configuration so the result can be repeated after code, instance, quota, or pool changes.

A load test passes only if both safety and liveness hold: limits remain bounded, and admitted work reaches the stated terminal target within its SLO. A system that protects the provider by letting the queue grow forever is safe but not adequately provisioned.

---

## 7. A capacity plan is an owned production contract

Keep one worksheet or configuration record per workload class with the default operational fields:

```text
owner and on-call route
arrival envelope and queue-age SLO
service-time and memory percentiles
per-pod execution slots and pool sizes
minimum/maximum replicas
tenant/global/provider admission limits
retry, fan-out, retention, and cost budgets
last load-test evidence and review trigger
```

Assign an owner to each hard ceiling. Provider quota changes belong to the team that owns the provider account; database connection allocation belongs to the database/platform owner; tenant product limits belong to the product/service owner. An unlabeled limit becomes an emergency guess during an incident.

Review the plan when task code, payload distribution, worker instance type, concurrency model, provider contract, retry policy, fan-out bound, or SLO changes. Do not wait for calendar review when a code change doubles service demand.

Emergency overrides have a scope, actor, reason, maximum value, and expiry. Raising a tenant or global cap without raising the constraining downstream budget only moves the overload deeper into the system.

---

## 8. Know when arithmetic is enough and when isolation must change

For tens of jobs per day with no latency SLO, measure service time and memory, choose a small bounded worker pool, and alert on oldest age. A full autoscaler and forecasting model may cost more than the idle capacity it saves.

Use separate worker deployments when workloads require different execution models, resources, credentials, or scaling signals. Use separate provider accounts, databases, or clusters when a shared hard ceiling cannot satisfy incompatible tenant or regulatory guarantees. Use a managed execution platform when the team cannot own worker lifecycle, scaling, and dependency budgets—but still keep application admission and business-effect idempotency explicit.

⚠️ Scaling on CPU alone leaves I/O-bound queues stuck while CPU looks idle.

⚠️ Scaling on queue depth alone launches workers past database and provider ceilings, increasing pool wait and retry load.

⚠️ Using mean service time hides the long tail that holds leases, memory, and termination grace periods.

⚠️ Treating maximum replicas as infinite makes every local connection pool and semaphore an unbounded global multiplier.

Do not promise a queue-age SLO for work whose arrival bound, retry/fan-out amplification, or downstream capacity is unknown. Admit less, change the contract, or isolate the workload until the capacity model is finite and testable.

---

**Next**: [Part 8: Failure Injection and Testing](../08_failure_injection_and_testing.md)
