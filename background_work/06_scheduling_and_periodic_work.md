# Scheduling Creates Durable Firings; Workers Execute Them

> **Who this is for**: Engineers implementing periodic work without confusing calendar rules, delivery, and execution.

Before reading this, choose an execution pool in **[Task Execution Models](05_task_execution_models.md)** and a delivery mechanism in **[Queue and Worker Architectures](04_queue_and_worker_architectures.md)**.

---

## 1. A scheduler firing is an intent, not an execution guarantee

A nightly report is configured for 03:00. Two scheduler replicas wake together, both enqueue it, and tenants receive duplicate email. The next night the scheduler is down at 03:00 and no report is created at all. Cron syntax was correct; the missing contract was how a calendar occurrence becomes one durable unit of work.

Separate the responsibilities:

```text
schedule definition
  → calculates a logical scheduled_for instant
  → persists one firing with a unique key
  → creates job/outbox atomically
  → worker executes at least once
```

The scheduler decides **when work becomes owed**. Queue and worker reliability decide how the owed work runs.

> **The near-miss**: a persistent scheduler store preserves definitions and next-run metadata. It does not automatically make the side effect durable or exactly once; the process can still die between selecting a due schedule and recording its work.

---

## 2. Cron and intervals answer different time questions

| Trigger | Meaning | Good fit | First surprise |
|---|---|---|---|
| Calendar/cron | A wall-clock rule such as “03:00 Europe/Athens every day” | Human/business calendar promises | DST creates missing or repeated local times |
| Fixed interval | A duration since an anchor, such as every 15 minutes | Polling, refresh, maintenance cadence | Drift when calculated from completion time |
| One-shot date/time | One known instant | Reminder or delayed command | Restart/misfire policy still matters |
| Workflow timer | Wake this workflow after/until a durable point | Timeout, approval deadline, saga wait | Belongs to workflow history, not a global cron table |

For an interval, calculate occurrences from a stable anchor:

```text
scheduled_for(n) = anchor + n × interval
```

Do not use “last completion + interval” unless completion-based drift is the actual requirement. A slow 15-minute refresh would otherwise slide later every run.

For calendar schedules, store the original rule and an IANA timezone name such as `Europe/Athens`, not a fixed UTC offset. Timezone rules can change; preserve the rule version or re-evaluate future occurrences deliberately when the timezone database changes.

> **Key insight**: A periodic job is identified by its logical occurrence, not by the moment a scheduler process happened to notice it.

---

## 3. DST policy is part of the business contract

Local wall time has two exceptional cases:

```text
spring forward: a local time may not exist
fall back:       one local label may occur twice with different UTC offsets
```

Use a concrete schedule: `daily-report` at **03:30 in `Europe/Athens`**. In 2026, Athens jumps from `02:59:59+02:00` to `04:00:00+03:00` on March 29, then repeats the 03:00 hour with a different offset on October 25.

| Case and policy | Requested local label | Resolved zoned time | UTC `scheduled_for` | Durable firing key |
|---|---|---|---|---|
| Spring gap; shift to the next valid local instant | `2026-03-29 03:30` | `2026-03-29 04:00+03:00[Europe/Athens]` | `2026-03-29T01:00:00Z` | `(daily-report, 2026-03-29T01:00:00Z)` |
| Fall duplicate; run the first occurrence | `2026-10-25 03:30` | `2026-10-25 03:30+03:00[Europe/Athens]` | `2026-10-25T00:30:00Z` | `(daily-report, 2026-10-25T00:30:00Z)` |
| Fall duplicate; second occurrence if policy is `both` | `2026-10-25 03:30` | `2026-10-25 03:30+02:00[Europe/Athens]` | `2026-10-25T01:30:00Z` | `(daily-report, 2026-10-25T01:30:00Z)` |

The wall-clock rule, the resolved zoned time, and the firing identity are three different facts. `Europe/Athens` supplies the date-specific rules; `03:30+02:00` supplies only one fixed offset; `scheduled_for` is the unique UTC instant used for deduplication. If the policy is “both,” the repeated local label deliberately creates two distinct keys. If it is “first,” a second scheduler pass converges on the first key rather than inventing another firing.

Choose and store an explicit policy:

| Case | Available policy | Sensible default |
|---|---|---|
| Nonexistent local time | Skip, shift to next valid time, or run at a fixed UTC instant | Shift only if “once per local day” matters; otherwise use UTC |
| Ambiguous repeated time | First occurrence, second occurrence, or both | Once at the first occurrence unless the product requires both |
| Timezone rule changes | Recompute future occurrences or pin calculated instants | Preserve past firings; recompute only future rows |

Use UTC for machine-oriented intervals and when wall-clock time has no user meaning. Use a named local zone when the promise is “business day at local time,” and test both DST transitions for that zone.

⚠️ Storing only `03:00+02:00` turns a region's changing offset into a permanent one and shifts the job after daylight-saving changes. It also cannot say whether a repeated local time meant the first or second occurrence.

---

## 4. Misfire and catch-up policy bound restart behavior

A **misfire** is an occurrence whose scheduled time passed while the scheduler could not create it. Catch-up decides how many missed occurrences become work after recovery.

| Policy | Restart after five missed occurrences | Use when |
|---|---|---|
| Skip | Create none; continue with the next future occurrence | Stale work has no value |
| Latest/coalesce | Create only the most recent missed occurrence | Refreshing current state; default for cache/index maintenance |
| Bounded catch-up | Create at most N oldest/newest occurrences | Each period matters but recovery load must be capped |
| Full catch-up | Create all five | Financial/data records require every period and capacity is planned |

Also define a **misfire grace**: after that age, an occurrence is too late even if catch-up would normally include it. Keep a per-pass creation limit so a month-long outage cannot enqueue millions of jobs at once.

For data pipelines, catch-up and backfill are often first-class requirements. For “send today's reminder,” yesterday's missed firing is usually stale and should not run.

---

## 5. Overlap policy decides what happens while the last run is active

Misfire concerns a scheduler that noticed late. **Overlap** concerns a new occurrence arriving before prior work terminated.

| Policy | New occurrence while active | Good fit |
|---|---|---|
| Serialize | Persist it, but do not claim until the previous run terminates | Default for mutation of one shared target |
| Skip | Record `SKIPPED_OVERLAP`; create no job | Expensive refresh where the active run will make state current |
| Coalesce | Keep one pending occurrence representing all arrivals | Polling/current-state refresh |
| Allow parallel | Create and execute independently | Partitioned periods or immutable outputs |

Implement the policy against durable firing/job rows, not an in-process mutex. A per-schedule advisory lock can serialize creation, but an active-run unique constraint or conditional claim must enforce execution across replicas.

For the report example, output keys include tenant and report date, so different dates may run independently while duplicate work for the same tenant/date is excluded by the operation key.

---

## 6. One unique firing survives multiple scheduler replicas

Store definitions and logical occurrences separately:

```sql
CREATE TABLE schedules (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    trigger_spec JSONB NOT NULL,
    timezone TEXT NOT NULL,
    misfire_policy TEXT NOT NULL,
    overlap_policy TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    next_fire_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE schedule_firings (
    id UUID PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    scheduled_for TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,              -- CREATED | SKIPPED_OVERLAP | CANCELLED
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (schedule_id, scheduled_for)
);
```

After calculating a due `:scheduled_for`, every replica may attempt the same transaction. The unique constraint chooses one winner, and the job receives a row only from that winner:

```sql
WITH firing AS (
    INSERT INTO schedule_firings (
        id, schedule_id, scheduled_for, status
    )
    VALUES (
        :firing_id, :schedule_id, :scheduled_for, 'CREATED'
    )
    ON CONFLICT (schedule_id, scheduled_for) DO NOTHING
    RETURNING id, schedule_id, scheduled_for
),
job AS (
    INSERT INTO jobs (
        id, workflow_run_id, step, status, idempotency_key, payload
    )
    SELECT :job_id,
           NULL,
           'nightly_report',
           'PENDING',
           schedule_id::text || ':' ||
               to_char(
                   scheduled_for AT TIME ZONE 'UTC',
                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
               ),
           jsonb_build_object(
               'schedule_firing_id', id,
               'scheduled_for', scheduled_for
           )
    FROM firing
    ON CONFLICT (idempotency_key) DO NOTHING
)
SELECT id, scheduled_for FROM firing;
```

One returned row means this replica created the durable firing. Zero means another replica already did; that is successful deduplication, not an error.

Update `schedules.next_fire_at` from the same occurrence using expected old value or a short row lock. If that cursor update is repeated after a crash, recalculating the old occurrence only hits the unique constraint and cannot duplicate its job.

Exactly one **firing row** does not mean exactly one execution attempt. Workers still use leases, retry, and idempotency because delivery and execution are at least once.

---

## 7. Scheduler tests use logical time and replica races

Before production, prove:

1. Two replicas create one `(schedule_id, scheduled_for)` row and one job.
2. A crash after firing insert but before cursor update does not duplicate work on restart.
3. Spring-forward nonexistent time follows the configured skip/shift policy.
4. Fall-back repeated time produces one or two firings according to policy, with distinct UTC instants when two are intended.
5. Five missed periods produce exactly the configured skip/coalesce/bounded/full catch-up set.
6. An active prior run follows serialize/skip/coalesce/parallel policy.
7. Disabled schedules create no new firing even when an old cursor is due.

**How you know it is working**: expose scheduler lag (`now - oldest due next_fire_at`), firing creation rate by outcome, duplicate-conflict count, misfires by policy, catch-up backlog, and time since each scheduler replica last scanned. A healthy job queue with growing scheduler lag means work is never being created.

⚠️ Running the same uncoordinated scheduler inside every web replica duplicates logical firings even if each process is individually correct.

Do not use a global scheduler for a timer that is part of one workflow's durable state; let the workflow engine/history own it. Do not use cron to poll continuously when an event or queue can represent the change directly.

---

**Next**: [Reliability Deep Dives](reliability/README.md)
