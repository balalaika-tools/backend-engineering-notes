# Reliability Tests Must Interrupt the System Between Durable Facts

> **Who this is for**: Engineers validating a background-work recovery contract before production.

Before reading this, follow the expected evidence chain in **[End-to-End Database-Backed Workflow](state_machines/04_end_to_end_workflow.md)**.

---

## 1. Happy-path tests cannot prove crash recovery

A unit test makes the provider return `503` and confirms the worker calls `retry()`. Production kills the worker after the provider committed but before local completion—a point the mock never represented. The retry path runs and duplicates the effect.

Reliability tests inject failure at **boundaries between durable facts**:

```text
DB transition committed | outbox not marked
job claimed             | first heartbeat not sent
provider effect exists  | local result absent
result persisted        | workflow not completed
cancellation committed  | old worker returns
```

Each test asserts final rows and external effect count, not only raised exceptions or method calls.

> **The near-miss**: mocking the repository proves that application code called `complete(job)`. It cannot prove the SQL affected one row, that another connection lost the race, or that state and outbox committed atomically.

---

## 2. Use four test layers for four failure boundaries

| Layer | Real components | Best assertions |
|---|---|---|
| Pure transition tests | Versioned decision function | Legal events, guards, derived target/commands |
| Database integration | Real PostgreSQL, separate connections | CAS winners, token fencing, unique keys, atomic CTEs |
| Worker integration | Real job repository + controllable provider stub | Heartbeat loss, timeout, retry schedule, result persistence |
| Process/system test | Worker subprocess/container + broker/store | Kill points, redelivery, drain, reconciliation, DLQ/redrive |

Use the real database for locking, transaction, and constraint semantics. A broker emulator is useful only if it reproduces acknowledgement, visibility, and redelivery behavior you depend on. Run a smaller provider contract suite against the provider's sandbox when its idempotency semantics are a business guarantee.

> **Key insight**: A failure test is complete only when it proves both safety (“the effect did not happen twice”) and liveness (“the owed work eventually reached an explainable terminal state”).

---

## 3. Fault hooks make the dangerous windows deterministic

Add test-only barriers at named boundaries rather than random `sleep()` calls:

```text
before_transition_commit
after_transition_before_response
after_publish_before_outbox_mark
after_claim_before_heartbeat
after_provider_effect_before_result
after_result_before_completion
before_retry_transition
before_completion_compare_and_set
```

A barrier exposes “arrived” and “continue” controls. The test waits until the worker reaches the exact point, kills/pauses/races it, changes durable state, then releases it. Compile or dependency-inject hooks out of production paths; never leave an HTTP “crash here” endpoint deployed.

For time-based behavior, inject a clock into policy calculations and use database-controlled timestamps in integration tests. Do not make the suite wait through real 90-second leases or one-hour retry budgets.

---

## 4. A real PostgreSQL race proves claim and fencing semantics

Save this as `test_lease_race.py`, set `DATABASE_URL` to a disposable PostgreSQL database, install `psycopg[binary]`, and run `python test_lease_race.py`. It creates a uniquely named schema, opens separate connections, races two claimers, expires the winner, and proves its stale completion loses.

```python
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import uuid

import psycopg
from psycopg import sql


DATABASE_URL = os.environ["DATABASE_URL"]
SCHEMA = f"failure_test_{uuid.uuid4().hex}"
barrier = threading.Barrier(2)


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, autocommit=False)


def claim(worker_id: str) -> tuple[str, str] | None:
    token = str(uuid.uuid4())
    barrier.wait()
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    WITH candidate AS (
                        SELECT id
                        FROM {}.jobs
                        WHERE status = 'PENDING'
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE {}.jobs AS j
                    SET status = 'RUNNING',
                        worker_id = %s,
                        attempt_token = %s,
                        lease_expires_at = clock_timestamp() + interval '10 seconds'
                    FROM candidate
                    WHERE j.id = candidate.id
                    RETURNING j.id, j.attempt_token
                    """
                ).format(sql.Identifier(SCHEMA), sql.Identifier(SCHEMA)),
                (worker_id, token),
            )
            row = cursor.fetchone()
        connection.commit()
    return row


def main() -> None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as setup:
        setup.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)))
        setup.execute(
            sql.SQL(
                """
                CREATE TABLE {}.jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    worker_id TEXT,
                    attempt_token TEXT,
                    lease_expires_at TIMESTAMPTZ,
                    result_ref TEXT
                )
                """
            ).format(sql.Identifier(SCHEMA))
        )
        setup.execute(
            sql.SQL("INSERT INTO {}.jobs (id, status) VALUES ('job-55', 'PENDING')").format(
                sql.Identifier(SCHEMA)
            )
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["worker-A", "worker-B"]))
        winners = [row for row in results if row is not None]
        assert len(winners) == 1, results
        old_token = winners[0][1]

        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                sql.SQL(
                    "UPDATE {}.jobs SET lease_expires_at = clock_timestamp() - interval '1 second'"
                ).format(sql.Identifier(SCHEMA))
            )
            connection.execute(
                sql.SQL(
                    """
                    UPDATE {}.jobs
                    SET status = 'PENDING', worker_id = NULL,
                        attempt_token = NULL, lease_expires_at = NULL
                    WHERE id = 'job-55' AND status = 'RUNNING'
                      AND lease_expires_at <= clock_timestamp()
                    """
                ).format(sql.Identifier(SCHEMA))
            )
            new_token = str(uuid.uuid4())
            connection.execute(
                sql.SQL(
                    """
                    UPDATE {}.jobs
                    SET status = 'RUNNING', worker_id = 'worker-C',
                        attempt_token = %s,
                        lease_expires_at = clock_timestamp() + interval '10 seconds'
                    WHERE id = 'job-55' AND status = 'PENDING'
                    """
                ).format(sql.Identifier(SCHEMA)),
                (new_token,),
            )

            stale = connection.execute(
                sql.SQL(
                    """
                    UPDATE {}.jobs SET status = 'SUCCEEDED', result_ref = 'stale'
                    WHERE id = 'job-55' AND status = 'RUNNING' AND attempt_token = %s
                    """
                ).format(sql.Identifier(SCHEMA)),
                (old_token,),
            )
            assert stale.rowcount == 0

            current = connection.execute(
                sql.SQL(
                    """
                    UPDATE {}.jobs SET status = 'SUCCEEDED', result_ref = 'current'
                    WHERE id = 'job-55' AND status = 'RUNNING' AND attempt_token = %s
                    """
                ).format(sql.Identifier(SCHEMA)),
                (new_token,),
            )
            assert current.rowcount == 1
        print("one claim winner; stale token rejected; current token completed")
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as cleanup:
            cleanup.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(SCHEMA))
            )


if __name__ == "__main__":
    main()
```

Success is the printed line and a zero exit code. If both claimers return a row, the claim is not atomic. If the stale update changes one row, a terminal path omitted the token fence.

---

## 5. The production acceptance matrix names injection and evidence

The first seven rows are the **default recovery suite**. Add fan-out, scheduler, authorization, and tenant-isolation rows whenever the corresponding capability exists.

| Scenario | Deterministic injection | Safety assertion | Liveness assertion |
|---|---|---|---|
| **Crash/redelivery after external effect** | Provider stub commits, barrier blocks response persistence, kill worker | Effect count per operation key is one | Redelivery recovers provider result and completes |
| **Lease contention** | Two DB connections cross a barrier before claim | One current token; one claim winner per row | Losing worker claims other work or returns idle |
| **Lost heartbeat** | Heartbeat repository returns zero while provider task is paused | Local task cancels; old token makes no terminal write | Lease expiry/reconciler creates a new attempt |
| **Cancellation race** | Cancel and complete wait on one barrier with same expected version | One CAS changes one row; loser changes zero | Winner reaches `CANCELLED` or `COMPLETED` with history |
| **Outbox gap** | Broker confirms, then outbox mark raises/connection closes | Duplicate message creates no second job/effect | Expired publisher claim republishes and marks terminally |
| **Retry exhaustion** | Fake clock advances through attempt and age budgets | No claim before schedule or after terminal failure | Job reaches `FAILED` with terminal reason and DLQ evidence |
| **Redrive** | Operator command races stale delivery after cause fix | Original operation key is preserved; stale workflow rejected | New inspected attempt reaches terminal state |
| Fan-out join | Last two child completions cross one barrier | Counter increments once per item; one aggregate job | Group eventually aggregates or fails by policy |
| Scheduler replica race | Two schedulers create same logical occurrence | One `(schedule_id, scheduled_for)` and one job | Cursor advances; future occurrence still fires |
| Unauthorized/cross-tenant command | Principal for tenant A submits B's resource to create, approve, cancel, or redrive | Denial creates zero workflow, job, outbox, or provider rows for B | A later authorized command can still progress normally |
| Tenant flood | A fills pending/in-flight budget while B submits latency-sensitive work | Tenant/global reservations and provider permits remain bounded | B starts and completes within its queue-age SLO |

For each scenario, assert the complete row set: workflow state/version, transition history, job status/token/attempt, outbox/inbox evidence, provider operation, and result reference. A final API response alone can hide an inconsistent orphan row.

The authorization and tenant-isolation contracts are defined in [Production Operations](operations/README.md). Their tests assert the absence or fair ordering of durable work, not only worker behavior after a message already exists.

---

## 6. Provider stubs expose ambiguity deliberately

A useful stub records effects by idempotency key and offers controls for:

- Commit effect, then close the connection without a response.
- Return the original result on same-key/same-hash replay.
- Reject same-key/different-hash reuse.
- Delay longer than a lease while allowing heartbeats.
- Return `429` with `Retry-After`, transient `503`, and permanent validation errors.
- Expose effect count, request count, stored request hash, and provider operation ID.

Do not configure the stub to be more helpful than the real provider. If production cannot query by idempotency key after a timeout, the test must recover by repeating the same request rather than using a fictional lookup endpoint.

---

## 7. Tests must leave inspectable evidence on failure

Log the random schema/run IDs, barrier reached, child process PID, exact fault hook, and stable operation key. Preserve container logs and database snapshots as test artifacts when a system test fails. A flaky “timed out waiting for worker” result teaches nothing unless it also shows which durable evidence was missing.

Run fast transition/database races and authorization-denial tests on every change. Run process-kill, broker-redelivery, tenant-flood, and maximum-replica limit tests in CI or a scheduled pre-production suite. Re-run the provider contract suite when SDK/provider idempotency behavior changes.

⚠️ Random process killing without named boundaries creates nondeterministic coverage: many runs die before any interesting state and the dangerous window may never execute.

⚠️ Tests that clean all rows before assertions erase the evidence needed to debug the failure. Clean only after capture, using a unique namespace per test.

Do not unit-test database isolation with mocks. Do not require every pull request to run an hour-long chaos suite; keep a layered test pyramid while ensuring the full acceptance matrix runs before production changes.

**How you know the suite is working**: deliberately remove one attempt-token predicate, request-hash comparison, or outbox dependency and see the corresponding test fail for the expected invariant—not merely by timeout.

---

**Next**: [Part 9: Decision Guide](09_decision_guide.md)
