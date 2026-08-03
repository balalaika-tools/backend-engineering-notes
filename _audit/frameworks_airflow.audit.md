# background_work/frameworks/airflow/README.md
Summary: 0 critical, 0 high, 0 med, 0 low

NO-ACTION: the one-file index accurately states the Airflow note's scope and prerequisites.

# background_work/frameworks/airflow/overview.md
Summary: 0 critical, 2 high, 3 med, 0 low

FIX-HIGH: §5's hardened task says a triggered run may have no data interval but then indexes `context["data_interval_start"]` whenever `conf.partition` is absent — explicitly reject a manual/event run that supplies neither a partition nor an interval, because Airflow 3.3 documents that manual and asset-triggered runs with `logical_date=None` may omit interval fields. Source: https://airflow.apache.org/docs/apache-airflow/stable/release_notes.html, checked 2026-08-03.
FIX-HIGH: §12 is titled as a testing section but contains no executable test — add a parse/import test and `dag.test()` example, state what successful task/dependency execution looks like, and include the common failure tell; Airflow 3.3 documents `dag.test()` as the simulated DAG-run entry point. Source: https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html, checked 2026-08-03.
FIX-MED: `check_partition` is configured with `retries=2` while the next sentence says its deterministic `ValueError` should normally avoid automatic retries — make the code match the policy by disabling retries for deterministic data-quality failure or raising a separately classified transient exception.
FIX-MED: §8 explains only traditional sensors and deferrable operators, but the current Task SDK also has native async tasks since Airflow 3.2 and resumable-task support in Airflow 3.3 — add the decision boundary between async I/O inside one worker slot, deferral that releases the slot, and resumable external-job recovery. Source: https://airflow.apache.org/docs/task-sdk/stable/deferred-vs-async-operators.html, checked 2026-08-03.
FIX-MED: §10 tells FastAPI to trigger through the stable API but never shows the current request or its idempotency result — add `POST /api/v2/dags/{dag_id}/dagRuns` with a stable `dag_run_id`, OAuth-authenticated client code, and handling for the documented `409 Conflict` on a duplicate run ID. Source: https://airflow.apache.org/docs/apache-airflow/stable/stable-rest-api-ref.html, checked 2026-08-03.
