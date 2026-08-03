# Durable Fan-Out Persists the Set Before Parallel Work Starts

> **Who this is for**: Engineers splitting one workflow step into parallel child jobs that must join exactly once.

Before reading this, understand atomic job creation in **[Atomic Transitions and Outbox](reliability/01_atomic_transitions_and_outbox.md)** and bounded execution in **[Task Execution Models](05_task_execution_models.md)**.

---

## 1. `gather()` cannot recover a distributed join

A workflow creates 500 child jobs in memory, publishes 300, and crashes. Some children finish, but the replacement process does not know the original set, which 200 were never published, or whether “all complete” is true. Parallelism was easy; durable membership and completion were missing.

Persist the expected child set before making any child claimable:

```text
parent transition
  → validate and bound child keys
  → insert fanout group + every expected item + every child job atomically
  → workers complete items idempotently
  → last durable completion creates one aggregate job
```

> **The near-miss**: a counter named `remaining=500` cannot prove which children completed. Duplicate deliveries can decrement twice, and a missing child cannot be distinguished from an unknown original member.

---

## 2. The expected set is authoritative

```sql
CREATE TABLE fanout_groups (
    id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL REFERENCES workflow_runs(id),
    step TEXT NOT NULL,
    status TEXT NOT NULL,               -- OPEN | JOIN_QUEUED | SUCCEEDED | FAILED
    expected_count INTEGER NOT NULL CHECK (expected_count > 0),
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    failure_policy TEXT NOT NULL,       -- FAIL_FAST | COLLECT_ALL
    input_set_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    UNIQUE (workflow_run_id, step, input_set_hash)
);

CREATE TABLE fanout_items (
    group_id UUID NOT NULL REFERENCES fanout_groups(id),
    item_key TEXT NOT NULL,
    status TEXT NOT NULL,               -- PENDING | SUCCEEDED | FAILED
    result_ref TEXT,
    error_class TEXT,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (group_id, item_key)
);
```

`expected_count` is checked against the item rows by reconciliation. `input_set_hash` makes command replay converge on the same group, while the primary key identifies each child completion.

Store stable item keys, not array positions that change when input order changes. Sort and hash the normalized set before creation.

> **Key insight**: A durable join is set reconciliation, not arithmetic. The counter accelerates the decision; item identity proves it.

---

## 3. Bound the set before committing any child

The application applies a product limit such as `MAX_CHILDREN = 1_000`, rejects duplicates, and estimates payload/provider cost before opening the transaction. The database repeats the hard bound:

```sql
WITH input AS (
    SELECT value AS item_key
    FROM jsonb_array_elements_text(:item_keys::jsonb)
),
new_group AS (
    INSERT INTO fanout_groups (
        id, workflow_run_id, step, status,
        expected_count, failure_policy, input_set_hash
    )
    SELECT :group_id,
           :run_id,
           'research_sources',
           'OPEN',
           count(*),
           :failure_policy,
           :input_set_hash
    FROM input
    HAVING count(*) BETWEEN 1 AND :max_children
    ON CONFLICT (workflow_run_id, step, input_set_hash) DO NOTHING
    RETURNING id
),
items AS (
    INSERT INTO fanout_items (group_id, item_key, status)
    SELECT new_group.id, input.item_key, 'PENDING'
    FROM new_group CROSS JOIN input
    RETURNING group_id, item_key
),
child_jobs AS (
    INSERT INTO jobs (
        id, workflow_run_id, step, status, idempotency_key, payload
    )
    SELECT gen_random_uuid(),
           :run_id,
           'research_one_source',
           'PENDING',
           items.group_id::text || ':' || items.item_key,
           jsonb_build_object(
               'fanout_group_id', items.group_id,
               'item_key', items.item_key
           )
    FROM items
    ON CONFLICT (idempotency_key) DO NOTHING
)
SELECT id FROM new_group;
```

Zero rows mean the group already exists or the count is outside the bound. The command service distinguishes those cases by reading the unique key and input count; it never publishes a partial set.

For millions of items, do not insert one giant transaction. Persist an immutable manifest in object storage, create bounded pages with durable cursors, and keep a fixed maximum of uncompleted children. The manifest hash and expected page/item counts remain authoritative.

---

## 4. Execution concurrency is separate from fan-out size

A group may contain 1,000 expected items while only 20 run concurrently:

```text
1,000 durable fanout_items/jobs
       │
       ├── 20 claimed RUNNING
       ├── bounded local/provider concurrency
       └── 980 remain PENDING and claimable by the fleet
```

Claim only free capacity and enforce per-tenant and global provider limits. Do not claim all 1,000 into one process and renew their leases while 980 wait locally.

Use separate limits for:

- Maximum children per group and per page.
- Active children per group, tenant, pod, and provider account.
- Total pending children per tenant.
- Result bytes retained before aggregation.
- Retry budget per child and aggregate cost budget per group.

⚠️ A single malformed request that expands to unbounded child jobs can exhaust database rows, queue throughput, provider quota, and money before worker concurrency becomes the bottleneck.

---

## 5. Each child completion is idempotent

The child job first persists its result behind its lease token. In the same transaction, it conditionally changes one fan-out item and increments the group only from that successful item update:

```sql
WITH completed_item AS (
    UPDATE fanout_items
    SET status = 'SUCCEEDED',
        result_ref = :result_ref,
        completed_at = now()
    WHERE group_id = :group_id
      AND item_key = :item_key
      AND status = 'PENDING'
    RETURNING group_id
),
advanced_group AS (
    UPDATE fanout_groups AS g
    SET completed_count = completed_count + 1,
        status = CASE
            WHEN completed_count + 1 = expected_count THEN 'JOIN_QUEUED'
            ELSE status
        END
    FROM completed_item
    WHERE g.id = completed_item.group_id
      AND g.status = 'OPEN'
    RETURNING g.id, g.status, g.expected_count, g.completed_count
),
join_job AS (
    INSERT INTO jobs (
        id, workflow_run_id, step, status, idempotency_key, payload
    )
    SELECT :join_job_id,
           g.workflow_run_id,
           'aggregate_research',
           'PENDING',
           g.id::text || ':aggregate',
           jsonb_build_object('fanout_group_id', g.id)
    FROM fanout_groups AS g
    JOIN advanced_group AS a ON a.id = g.id
    WHERE a.status = 'JOIN_QUEUED'
    ON CONFLICT (idempotency_key) DO NOTHING
)
SELECT id, status, completed_count, expected_count FROM advanced_group;
```

A duplicate completion finds the item already `SUCCEEDED`, so `completed_item` returns zero, the counter does not move, and no second join job is created. Concurrent last children serialize on the group-row increment; exactly one update observes `completed_count + 1 = expected_count`.

The production transaction also verifies the child job's current attempt token before `completed_item`, so a stale worker cannot contribute a result.

---

## 6. Failure policy is chosen before children start

For `FAIL_FAST`, the first permanent child failure conditionally marks the group `FAILED`, prevents new child claims, and schedules cancellation/cleanup for already running children. Their late completions are recorded as evidence but cannot queue the join.

For `COLLECT_ALL`, each terminal child increments `completed_count`; failures also increment `failed_count`. The last terminal child queues aggregation, and the aggregator decides whether a partial result is acceptable.

```text
FAIL_FAST:   one required child fails → group FAILED
COLLECT_ALL: all children terminal    → aggregate successes + failure evidence
```

Retries do not mark the item terminal. A child remains `PENDING` until its retry budget ends; only then does the group policy consume the failure.

---

## 7. The aggregate validates the set again

Before producing the parent result, load all items and prove:

```text
row_count = expected_count
terminal_count = expected_count
count(SUCCEEDED) = completed_count - failed_count
every required result_ref exists and matches its content hash
```

Then write the aggregate result and move `fanout_groups` from `JOIN_QUEUED` to `SUCCEEDED` behind the join job's attempt token. The parent workflow transition selects from that successful group update.

A reconciler flags:

- `expected_count` different from item-row count.
- All items terminal while group remains `OPEN`.
- `JOIN_QUEUED` without one aggregate job.
- Aggregate job present before all required items are terminal.
- Stale `OPEN` groups with no pending/running child jobs.

**How you know it is working**: a race test completes the last two children concurrently and observes one aggregate job; a duplicate-completion test leaves counters unchanged; a worker-crash test eventually accounts for every expected item.

Do not use durable fan-out for a small in-process calculation whose entire retry can safely restart. Do not fan out below the granularity where dispatch/storage overhead exceeds useful parallel work. Use a data orchestrator when the problem is a partitioned data pipeline with backfill and dataset lineage rather than a service workflow step.

---

**Next**: [Failure Injection and Testing](08_failure_injection_and_testing.md)
