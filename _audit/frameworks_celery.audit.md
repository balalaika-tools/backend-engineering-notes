# background_work/frameworks/celery/README.md
Summary: 0 critical, 0 high, 0 med, 0 low

NO-ACTION: the index accurately scopes the note to Celery delivery, execution, scheduling, and business-state boundaries.

# background_work/frameworks/celery/overview.md
Summary: 0 critical, 2 high, 2 med, 0 low

FIX-HIGH: §4's "hardened" task claims the domain step before the provider call but never releases or reschedules that claim on a transient exception — a Celery autoretry can then reload the already-`RUNNING` step, fail `claim_expected_step()`, return successfully, and leave the workflow stuck; define and implement the retry transition or carry a stable attempt token that the retry is allowed to resume.
FIX-HIGH: §4's `complete_step_if_owned()` receives no lease/attempt token and the example has no heartbeat or expiry path after worker loss — show concrete ownership SQL or label the block a skeleton and link directly to the lease protocol; as written, all of the correctness is hidden behind method names that cannot satisfy the note's own worker-loss guarantee.
FIX-MED: §10 passes only a static `schedule_key="nightly-cleanup"` and then tells the task to derive `nightly-cleanup:2026-08-03` — pass the scheduler's intended firing identity/logical date in the message, because deriving from the worker's current time changes after delayed delivery or retry.
FIX-MED: §14's worker test defines another toy `add` task instead of testing the note's `generate_report` task — add one application-task test that proves serialization, route selection, transient retry, and the durable status transition rather than only proving Celery's fixture works.
