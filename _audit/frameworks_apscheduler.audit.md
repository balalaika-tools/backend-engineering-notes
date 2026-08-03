# background_work/frameworks/apscheduler/README.md
Summary: 0 critical, 0 high, 0 med, 0 low

NO-ACTION: the index accurately scopes the note to APScheduler triggers, persistence, misfires, overlap, and deployment boundaries.

# background_work/frameworks/apscheduler/overview.md
Summary: 2 critical, 1 high, 2 med, 0 low

FIX-CRITICAL: "Solution 3" says Dramatiq handles deduplication, but Dramatiq assumes actors are idempotent and may deliver the same message multiple times; the shown `nightly_cleanup_task.send()` has no stable firing key or uniqueness guard, so duplicate scheduler instances create duplicate business effects — persist a unique `job_name + scheduled_fire_time` record/outbox row and make the actor claim it idempotently. Source: https://dramatiq.io/best_practices.html, checked 2026-08-03.
FIX-CRITICAL: the pause, resume, remove, and run-now FastAPI endpoints are shown without authentication or authorization — make them explicitly admin-only, enforce authorization in code, and warn that exposing them lets an arbitrary caller disable or trigger billing/cleanup jobs.
FIX-HIGH: the Redis-lock heartbeat is only a lambda handed to an undefined `do_cleanup()` and provides no periodic renewal loop or fencing after ownership is lost — show an actual renewal task that aborts on failed extension, and state that critical effects still need a durable unique firing record/idempotency key because a distributed lock does not provide exactly-once execution.
FIX-MED: the FastAPI lifespan creates `AsyncIOScheduler()` without the explicit timezone that the preceding section calls mandatory, so its `hour=4` example reverts to host-local time — construct it with `timezone="UTC"` and make the run-now timestamp timezone-aware.
FIX-MED: the dedicated-scheduler example likewise creates `AsyncIOScheduler()` with host-local time and sends a bare Dramatiq message — pin the timezone and carry the scheduled fire time/stable idempotency key into the durable job created by each firing.
