# Admission Control Keeps One Tenant from Becoming Everyone's Backlog

> **Who this is for**: Engineers running shared background workers where tenants, workload classes, or provider accounts compete for finite capacity.

Before reading this, understand bounded execution in **[Task Execution Models](../05_task_execution_models.md)** and bounded expansion in **[Durable Fan-Out and Join](../07_durable_fanout_and_join.md)**.

---

## 1. Accepting durable work is a capacity promise

Tenant A submits one request that expands into 100,000 document jobs. The API returns `202 Accepted`, all rows become durable, and worker concurrency stays correctly bounded at 40. Tenant B's five interactive jobs are safe from memory exhaustion but wait behind hours of A's backlog.

Execution limits answer how much work may run now. **Admission control** answers how much work the system is willing to owe. A multi-tenant design needs both, plus a fair rule for choosing which admitted job runs next.

```text
request
  │
  ├── rate check ───────► how often may this tenant ask?
  ├── admission check ──► may the system owe this much more work?
  └── durable commit ───► tenant-scoped workflow/jobs/reservation
                              │
                              └── fair claim ──► leased execution slot
                                                     │
                                                     └── provider/global budget
```

> **The near-miss**: a per-tenant requests-per-minute limiter looks like tenant isolation. One allowed request can create a million children, hold gigabytes of retained results, or consume a month's provider budget. Request rate is only one input to admission.

---

## 2. Four limits form the production default

Start with the **four bold limits**. Add rate and retained-data controls when the workload makes them necessary.

| Limit | Protects | Enforcement point | Default? |
|---|---|---|:---:|
| **Accepted/pending work per tenant** | Shared backlog, database rows, future cost | Transaction that creates or expands durable work | ✓ |
| **In-flight work per tenant** | Fair execution and tenant-scoped downstream use | Claim/admission lease | ✓ |
| **Global/provider in-flight work** | Database, sockets, provider account | Before claim or irreversible provider call | ✓ |
| **Per-operation cost/fan-out budget** | Money and multiplicative expansion | Before child set or expensive effect is committed | ✓ |
| Request/event rate | Control-plane load and abuse | API, webhook, or scheduler admission boundary | |
| Retained bytes per tenant | Object store, result DB, audit/DLQ footprint | Result commit and retention policy | |

These limits count different facts. A tenant can be below its in-flight limit while its pending backlog grows without bound. A fleet can be below every per-tenant limit while all tenants together exceed the provider account's quota.

Model units that match the scarce resource. Jobs are useful for homogeneous work; tokens, document pages, expected CPU-seconds, bytes, or estimated cost are better when one job can be 1,000 times larger than another.

> **Key insight**: fairness begins when work becomes owed, not when a worker thread starts. Once unbounded work is durable, bounded concurrency prevents a crash but cannot restore another tenant's lost queue position or capacity budget.

---

## 3. Reserve hard backlog limits in the durable commit

A hard pending-work cap is a correctness rule: two concurrent requests must not both observe 900 used units under a 1,000-unit limit and each reserve 100. Keep the counter and work intent in one transactional authority when possible.

```text
tenant_work_budgets
tenant-A | pending=900 | pending_limit=1000 | version=18

request 1: reserve 100 ──┐
                         ├── conditional update; one winner advances to 1000
request 2: reserve 100 ──┘   loser creates no group, jobs, or outbox rows
```

The transaction follows one dependency chain:

```text
conditional budget reservation
          │ winner row
          ├──► create fan-out group or job intent
          ├──► append admission evidence
          └──► create outbox hint when a broker is used
```

Completion, cancellation before execution, and terminal rejection release the reserved units through idempotent conditional updates. Reconciliation compares reservations with non-terminal work so a process crash cannot leak capacity permanently.

Use Redis or another distributed limiter for high-rate, short-lived request windows and leased in-flight permits; the mechanics are covered in [Distributed Admission Control](../../fundamentals/fastapi/safe_and_scalable_api_calls/09_distributed_admission_control.md) and [Redis Rate Limiting](../../infrastructure/redis/05_rate_limiting.md). Do not let an expiring Redis counter be the only record of a hard durable-backlog or cost promise when the job itself lives in PostgreSQL. A Redis allow followed by a failed database commit needs a refund or expiry; a database commit followed by a lost Redis update needs reconciliation.

**How you know reservation works**: race two expansions whose combined size exceeds the remaining tenant budget. Exactly one reservation commits, rejected work creates no child/job/outbox rows, and cancelling the winner returns the same units once. The silent failure is a cap that rejects future work forever after crashes because reservations have no reconciliation path.

---

## 4. Fair claiming gives every active tenant progress

FIFO is fair by enqueue time, not by tenant. Strict priority is fair to the highest class until lower classes starve. A useful claim policy guarantees progress for each active tenant while still reserving capacity for latency-sensitive work.

Choose the smallest mechanism that meets the guarantee:

| Mechanism | Good fit | Boundary |
|---|---|---|
| Separate queues with reserved workers | A few stable service tiers or workload classes | Idle reserved capacity may be wasted |
| Per-tenant in-flight cap | Many tenants with roughly similar job costs | Does not choose fairly among tenants below the cap |
| Round-robin/deficit scheduling | Many active tenants and variable job cost | Requires durable scheduler state or a capable broker |
| Aging priority | Bulk work may wait, but not forever | Needs oldest-age measurement and careful tuning |
| Weighted fair sharing | Contracted tiers receive different shares | Weight mistakes can institutionalize starvation |

A practical default combines a global claim ceiling, a per-tenant in-flight ceiling, and reserved capacity for the interactive class. Within each eligible class, choose oldest work from the tenant with the least current share or rotate tenants explicitly. Measure oldest-ready age per tenant and class; total queue depth cannot reveal starvation.

Ordering requirements narrow the choice. Serialize only the entity that needs order—such as one account or workflow run—rather than routing the whole tenant through one worker. A hot entity should not consume the tenant's entire share while it waits for its own prior job.

---

## 5. Retries and fan-out spend the same tenant budget

Retries are not free capacity. A provider outage can turn one admitted operation into six executions, and a fan-out of 500 children can turn a small API request into 3,000 attempts.

Track at least two quantities:

```text
logical units owed       = durable business operations not terminal
execution units consumed = attempts, CPU-seconds, provider calls, or cost
```

The logical pending reservation normally remains held across retries; releasing it after the first failure would let the tenant admit new work while the old obligation still exists. Attempt and wall-clock budgets bound execution amplification. Provider `429` responses should reduce or delay execution globally rather than allowing every tenant's independent retry loop to synchronize into another burst.

Fan-out admission uses the declared or discovered child count before child rows are inserted. For unknown or huge sets, reserve a bounded page, persist an immutable manifest and cursor, and admit the next page only after capacity returns. The limits in [Durable Fan-Out and Join](../07_durable_fanout_and_join.md) remain authoritative per group; tenant budgets limit all groups together.

Cost attribution uses the tenant that owns the logical operation, even when a shared service account performs the provider call. Record estimated cost at admission and actual cost at completion so material underestimation can stop later pages rather than discovering the overrun after the whole fan-out finishes.

---

## 6. Reject, defer, or degrade deliberately

Backpressure is a product decision exposed at the admission boundary:

| Policy | Use when | Observable contract |
|---|---|---|
| Reject | The caller can retry and no work has been promised | `429` or domain quota response with limit and retry/reset guidance |
| Defer admission | The request itself may wait durably before expansion | One bounded parent request with an eligibility time; no hidden child explosion |
| Accept at lower quality | A cheaper/smaller execution still meets the contract | Response records chosen quality/model/page cap |
| Coalesce | New work supersedes equivalent pending work | Stable deduplication key points to one obligation |

Do not return `202 Accepted` and then silently drop work to simulate backpressure. Do not create all children and mark most `BLOCKED`; they already consume durable capacity and still need retention, cancellation, and observability.

Emergency global or tenant pauses are control commands with actor, reason, and expiry. A pause stops new admission and optionally new claims; it does not revoke an already committed external effect. Define whether running work drains, cancels at a safe point, or continues.

---

## 7. Isolation tests use competing tenants, not one busy queue

The default suite covers **atomic reservation**, **noisy-neighbor progress**, **global quota**, and **retry amplification**.

| Test | Workload | Safety assertion | Liveness assertion |
|---|---|---|---|
| **Atomic reservation** | Two requests race for the final tenant units | Committed reservations never exceed limit | Winner progresses; loser receives actionable rejection |
| **Noisy-neighbor progress** | A floods long jobs while B submits short jobs | A stays within pending/in-flight shares | B's oldest age remains within its SLO |
| **Global quota** | Many tenants reach provider boundary together | Total leased provider permits stay bounded | Permits expire/release and queued work resumes |
| **Retry amplification** | Provider returns `429`/`503` across tenants | Attempt and cost budgets remain bounded | Jittered recovery drains without synchronized bursts |
| Fan-out cap | One request declares or discovers an oversized set | No partial unbounded child set is committed | Bounded pages continue as capacity returns |
| Pause/override expiry | Operator pauses A or grants a temporary burst | Scope never affects B; override cannot outlive expiry | Normal policy resumes automatically |

Use a controllable worker clock and provider stub. Assert per-tenant ready age, reservations, in-flight leases, effect counts, and actual rows—not only response codes.

**How you know isolation is working**: under a sustained A overload, B continues to start and complete within its stated SLO, A receives explicit admission or delay signals, and global provider/database ceilings never exceed their configured bounds. If aggregate throughput looks healthy while one tenant's oldest age rises continuously, the system is not fair.

---

## 8. Do not build tenant scheduling before tenants compete

One internal workload with one trusted producer may need only a global pending cap and a global execution semaphore. Separate tenant counters and weighted scheduling add state, reconciliation, and operational knobs that are unjustified when no isolation contract exists.

Add tenant fairness when customers share workers or a workload class can materially delay another. Add cost budgets when input size or provider use varies enough that job counts lie. Use physically separate queues, worker pools, databases, accounts, or clusters when regulatory isolation or incompatible SLOs cannot be expressed safely as weights and counters.

⚠️ A per-pod semaphore multiplied by autoscaling can exceed the global provider limit even though every pod is locally correct.

⚠️ A strict high-priority queue can keep normal work pending forever while dashboards report excellent throughput.

⚠️ Releasing a tenant's pending reservation when an attempt fails, rather than when the logical operation terminates, lets retrying work escape its admission budget.

⚠️ Counting jobs instead of work units lets one oversized document or fan-out consume the same nominal share as one email while exhausting memory, time, and money.

---

**Next**: [Part 3: Capacity Planning and Autoscaling](03_capacity_planning_and_autoscaling.md)
