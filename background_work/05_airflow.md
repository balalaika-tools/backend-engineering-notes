# Apache Airflow — Workflow Orchestration

Airflow is a platform for authoring, scheduling, and monitoring **workflows** (DAGs). It's the answer to "I need these 20 tasks to run in the right order, with retries, dependencies, and a dashboard."

> **Airflow is an orchestrator, not a worker.** It tells *other things* what to run and when. The actual computation happens in external systems (databases, Spark, Kubernetes pods, your API, Dramatiq workers, etc.).

> **Version note:** These notes target **Airflow 2.x** (2.10), which most production deployments still run. **Airflow 3.0** went GA in April 2025 (current stable 3.2.x) and is a large release. The TaskFlow API, `@dag`/`@task`, dynamic mapping, `schedule=`, and `catchup=False` shown here all carry over. The main 3.x changes to know: the REST API moved to **`/api/v2`** (`/api/v1` removed; `logical_date` is now a required, nullable payload field on the trigger endpoint), **`SubDagOperator` was removed** (use TaskGroups), the webserver is now an `api-server` on a React UI, and the scheduler is service-oriented. Avoid `SubDagOperator` and `schedule_interval=` (renamed to `schedule=`) in any new DAGs regardless of version.

---

## 1. When Airflow vs When Dramatiq

| Dimension | Dramatiq | Airflow |
|-----------|----------|---------|
| **Mental model** | Task queue — "run this function somewhere" | Workflow engine — "run these steps in this order" |
| **Trigger** | Code sends a message (`.send()`) | Schedule (cron) or external trigger (API) |
| **Dependencies** | None (tasks are independent) or basic pipelines | Full DAG — fan-out, fan-in, branching, conditional |
| **Visibility** | Logs + optional dashboard | Rich web UI with Gantt charts, logs, task history |
| **Retries** | Per-task, built-in | Per-task, with configurable backoff and alerting |
| **State** | Stateless (message in, result out) | Stateful — tracks every run, every task instance |
| **Infrastructure** | Redis/RabbitMQ + worker processes | Scheduler + web server + metadata DB + executor |
| **Best for** | Web backend background jobs | Data pipelines, ETL, ML workflows, batch processing |

**Rule of thumb:**
- User clicks a button → something happens in the background → **Dramatiq**
- Every night at 3 AM → extract data → transform → load → send report → **Airflow**

---

## 2. Core Concepts

### DAG (Directed Acyclic Graph)

A DAG defines the **structure** of your workflow — which tasks exist and how they depend on each other. It does **not** define what happens inside each task.

```
extract_users ──► transform_users ──► load_users ──┐
                                                    ├──► send_report
extract_orders ─► transform_orders ─► load_orders ──┘
```

- **Directed:** A → B means B runs after A
- **Acyclic:** No circular dependencies
- **Graph:** Multiple paths, fan-out, fan-in

### Task

A single unit of work inside a DAG. Defined using **Operators** or the **TaskFlow API**.

### Operator

A template for a task. Airflow ships with many:

| Operator | Purpose |
|----------|---------|
| `PythonOperator` | Run a Python callable |
| `BashOperator` | Run a shell command |
| `PostgresOperator` | Execute SQL against PostgreSQL |
| `S3ToRedshiftOperator` | Copy data from S3 to Redshift |
| `KubernetesPodOperator` | Run a container in Kubernetes |
| `HttpOperator` | Make an HTTP request |
| `EmailOperator` | Send an email |

### Sensor

A special operator that **waits** for a condition before proceeding:

| Sensor | Waits for |
|--------|-----------|
| `S3KeySensor` | A file to appear in S3 |
| `ExternalTaskSensor` | Another DAG's task to complete |
| `HttpSensor` | An HTTP endpoint to return success |
| `SqlSensor` | A SQL query to return results |
| `FileSensor` | A file to appear on the filesystem |

### XCom (Cross-Communication)

How tasks pass small data to each other. TaskFlow API handles this automatically.

```
Task A produces: {"count": 42}
    │
    └── XCom ──► Task B reads: ti.xcom_pull(task_ids="task_a")
```

> **XCom is for metadata, not data.** Pass file paths, S3 keys, or row counts — not the actual dataset.

### Executor

Determines **how** tasks run:

| Executor | How it works | When to use |
|----------|-------------|-------------|
| `SequentialExecutor` | One task at a time, SQLite | Local development only |
| `LocalExecutor` | Parallel processes on one machine | Small deployments |
| `CeleryExecutor` | Distributes tasks to Celery workers | Multi-machine, traditional |
| `KubernetesExecutor` | Spins up a pod per task | Cloud-native, isolation |

---

## 3. TaskFlow API (Modern Airflow 2.x+)

The TaskFlow API uses `@task` decorators to define tasks as plain Python functions. XCom is handled automatically through return values and function arguments.

### Basic DAG

```python
from datetime import datetime
from airflow.decorators import dag, task


@dag(
    schedule="0 3 * * *",          # 3 AM daily
    start_date=datetime(2025, 1, 1),
    catchup=False,                  # don't backfill missed runs
    tags=["etl", "users"],
)
def user_etl():
    """Extract, transform, and load user data."""

    @task()
    def extract() -> list[dict]:
        """Pull raw data from source system."""
        import httpx
        response = httpx.get("https://api.source.com/users")
        return response.json()  # automatically pushed to XCom

    @task()
    def transform(raw_users: list[dict]) -> list[dict]:
        """Clean and normalize user records."""
        return [
            {
                "id": u["id"],
                "email": u["email"].lower().strip(),
                "name": u["full_name"],
                "created_at": u["signup_date"],
            }
            for u in raw_users
            if u.get("email")  # skip users without email
        ]

    @task()
    def load(users: list[dict]) -> int:
        """Write to the analytics database."""
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        hook = PostgresHook(postgres_conn_id="analytics_db")
        hook.insert_rows(
            table="dim_users",
            rows=[(u["id"], u["email"], u["name"], u["created_at"]) for u in users],
            target_fields=["id", "email", "name", "created_at"],
            replace=True,
            replace_index="id",
        )
        return len(users)

    @task()
    def notify(count: int):
        """Send a Slack notification with the result."""
        from airflow.providers.slack.hooks.slack_webhook import SlackWebhookHook
        hook = SlackWebhookHook(slack_webhook_conn_id="slack_data_team")
        hook.send(text=f"User ETL complete: {count} users loaded")

    # Define the DAG flow — return values wire up XCom automatically
    raw = extract()
    cleaned = transform(raw)
    row_count = load(cleaned)
    notify(row_count)


# Instantiate the DAG
user_etl()
```

### Key Points

- `@dag` replaces `DAG()` context manager
- `@task` replaces `PythonOperator`
- Return values become XCom — function arguments receive them
- The DAG flow is defined by calling tasks and passing their outputs as arguments
- `catchup=False` prevents Airflow from running every missed schedule since `start_date`

---

## 4. Operator-Based DAG (Classic Style)

For cases where you need non-Python operators or prefer explicit wiring:

```python
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.sensors.filesystem import FileSensor


with DAG(
    dag_id="classic_etl",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
) as dag:

    wait_for_file = FileSensor(
        task_id="wait_for_export",
        filepath="/data/exports/users_{{ ds }}.csv",
        poke_interval=60,        # check every 60 seconds
        timeout=3600,            # give up after 1 hour
        mode="reschedule",       # free up the worker slot while waiting
    )

    process = BashOperator(
        task_id="process_csv",
        bash_command="python /scripts/process_users.py --date {{ ds }}",
    )

    load_to_db = PostgresOperator(
        task_id="load_to_db",
        postgres_conn_id="analytics_db",
        sql="sql/load_users.sql",
        params={"date": "{{ ds }}"},
    )

    cleanup = BashOperator(
        task_id="cleanup",
        bash_command="rm -f /data/exports/users_{{ ds }}.csv",
        trigger_rule="all_done",  # run even if upstream failed
    )

    # Define dependencies
    wait_for_file >> process >> load_to_db >> cleanup
```

### Dependency Syntax

```python
# Chain
a >> b >> c            # a → b → c

# Fan-out
a >> [b, c, d]         # a → b, a → c, a → d

# Fan-in
[b, c, d] >> e         # b → e, c → e, d → e

# Full diamond — chaining works because `>>` returns the right-hand side
a >> [b, c] >> d       # a → b, a → c, b → d, c → d

# The expanded equivalent:
a >> [b, c]
[b, c] >> d
```

---

## 5. Common Patterns

### Branching (Conditional Execution)

```python
from airflow.decorators import dag, task
from airflow.operators.empty import EmptyOperator


@dag(schedule="@daily", start_date=datetime(2025, 1, 1), catchup=False)
def branching_example():

    @task.branch()
    def choose_path(**context) -> str:
        """Return the task_id to execute next."""
        day = context["logical_date"].weekday()
        if day < 5:
            return "weekday_processing"
        return "weekend_report"

    @task()
    def weekday_processing():
        print("Running weekday logic")

    @task()
    def weekend_report():
        print("Running weekend report")

    join = EmptyOperator(task_id="join", trigger_rule="none_failed_min_one_success")

    choice = choose_path()
    choice >> [weekday_processing(), weekend_report()] >> join

branching_example()
```

### Dynamic Task Mapping (Airflow 2.3+)

Generate tasks dynamically based on data — like a `map()` over your dataset:

```python
@dag(schedule="@daily", start_date=datetime(2025, 1, 1), catchup=False)
def dynamic_etl():

    @task()
    def get_partitions() -> list[str]:
        """Return a list of partition keys to process."""
        return ["us-east-1", "us-west-2", "eu-west-1"]

    @task()
    def process_partition(partition: str) -> dict:
        """Process one partition — this runs in parallel for each."""
        count = run_etl_for_region(partition)
        return {"partition": partition, "count": count}

    @task()
    def summarize(results: list[dict]):
        """Aggregate results from all partitions."""
        total = sum(r["count"] for r in results)
        print(f"Processed {total} records across {len(results)} partitions")

    partitions = get_partitions()
    results = process_partition.expand(partition=partitions)  # dynamic fan-out
    summarize(results)

dynamic_etl()
```

`.expand()` creates N task instances at runtime — one per partition. Airflow runs them in parallel (subject to executor limits).

### Trigger Another DAG

```python
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

trigger_downstream = TriggerDagRunOperator(
    task_id="trigger_reporting",
    trigger_dag_id="reporting_pipeline",
    conf={"source": "user_etl", "date": "{{ ds }}"},
    wait_for_completion=False,   # fire-and-forget
)
```

### Timeout and SLA

```python
@dag(
    schedule="0 3 * * *",
    start_date=datetime(2025, 1, 1),
    dagrun_timeout=timedelta(hours=2),    # kill the entire run after 2 hours
    sla_miss_callback=send_slack_alert,   # notify if SLA is breached
)
def pipeline_with_sla():

    @task(execution_timeout=timedelta(minutes=30), sla=timedelta(hours=1))
    def slow_step():
        """Must finish within 30 min. SLA alert if DAG exceeds 1 hour."""
        ...
```

---

## 6. Connections and Variables

Airflow manages credentials and configuration through **Connections** and **Variables**, stored in its metadata database.

### Connections

```python
# Access in code
from airflow.hooks.base import BaseHook

conn = BaseHook.get_connection("my_postgres")
print(conn.host, conn.port, conn.login, conn.password)

# Or use a Hook (handles connection management for you)
from airflow.providers.postgres.hooks.postgres import PostgresHook
hook = PostgresHook(postgres_conn_id="my_postgres")
df = hook.get_pandas_df("SELECT * FROM users")
```

### Variables

```python
from airflow.models import Variable

# Set
Variable.set("etl_batch_size", "1000")

# Get
batch_size = int(Variable.get("etl_batch_size", default_var="500"))
```

> **Performance note:** `Variable.get()` hits the metadata database every call. Use it in the task body, not at module level — or use Jinja templating: `{{ var.value.etl_batch_size }}`.

---

## 7. Templating (Jinja)

Airflow passes runtime context through Jinja templates. This is how tasks access execution date, DAG run config, etc.

```python
# In operators
BashOperator(
    task_id="process",
    bash_command="python etl.py --date {{ ds }} --run-id {{ run_id }}",
)

# Common template variables
# {{ ds }}              → execution date as YYYY-MM-DD
# {{ ds_nodash }}       → execution date as YYYYMMDD
# {{ logical_date }}    → datetime object (Airflow 2.2+)
# {{ run_id }}          → unique run identifier
# {{ params.my_key }}   → custom parameters
# {{ var.value.key }}   → Airflow Variable
# {{ conn.my_conn.host }} → Connection field
```

---

## 8. Airflow + FastAPI Integration

Airflow exposes a REST API. Your FastAPI app can trigger DAGs programmatically.

### Trigger a DAG from FastAPI

```python
import httpx
from fastapi import FastAPI

app = FastAPI()

AIRFLOW_URL = "http://airflow-webserver:8080/api/v1"  # Airflow 3.x: /api/v2
AIRFLOW_AUTH = ("admin", "admin")  # use a service account in production


@app.post("/pipelines/user-etl/trigger")
async def trigger_user_etl(date: str):
    """Trigger the user_etl DAG with a custom config."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{AIRFLOW_URL}/dags/user_etl/dagRuns",
            json={
                "conf": {"target_date": date},
                "logical_date": f"{date}T00:00:00Z",
            },
            auth=AIRFLOW_AUTH,
        )
        response.raise_for_status()
        return response.json()


@app.get("/pipelines/user-etl/status/{run_id}")
async def get_run_status(run_id: str):
    """Check the status of a DAG run."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{AIRFLOW_URL}/dags/user_etl/dagRuns/{run_id}",
            auth=AIRFLOW_AUTH,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "state": data["state"],        # running, success, failed
            "start_date": data["start_date"],
            "end_date": data["end_date"],
        }
```

### Pattern: FastAPI Submits, Airflow Orchestrates, Dramatiq Executes

```
User ──► FastAPI ──► Airflow (schedule + orchestrate)
                        │
                        ├── Task 1: extract (PythonOperator)
                        ├── Task 2: transform (KubernetesPodOperator)
                        └── Task 3: notify (Dramatiq .send())
                                        │
                                        ▼
                                  Dramatiq Worker
```

Use Airflow for the workflow graph. Use Dramatiq for the actual execution if the task needs retries, rate limiting, or your existing worker infrastructure.

---

## 9. Docker Compose Setup

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: airflow
      POSTGRES_PASSWORD: airflow
      POSTGRES_DB: airflow
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "airflow"]
      interval: 10s
      timeout: 5s
      retries: 5

  airflow-init:
    image: apache/airflow:2.10-python3.12
    entrypoint: /bin/bash
    command: >
      -c "airflow db migrate &&
          airflow users create
            --username admin
            --password admin
            --firstname Admin
            --lastname User
            --role Admin
            --email admin@example.com"
    environment: &airflow-env
      AIRFLOW__CORE__EXECUTOR: LocalExecutor
      AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow@postgres:5432/airflow
      AIRFLOW__CORE__LOAD_EXAMPLES: "false"
      AIRFLOW__API__AUTH_BACKENDS: airflow.api.auth.backend.basic_auth
    depends_on:
      postgres:
        condition: service_healthy

  airflow-webserver:
    image: apache/airflow:2.10-python3.12
    command: webserver
    ports:
      - "8080:8080"
    environment: *airflow-env
    volumes:
      - ./dags:/opt/airflow/dags
      - ./plugins:/opt/airflow/plugins
    depends_on:
      airflow-init:
        condition: service_completed_successfully

  airflow-scheduler:
    image: apache/airflow:2.10-python3.12
    command: scheduler
    environment: *airflow-env
    volumes:
      - ./dags:/opt/airflow/dags
      - ./plugins:/opt/airflow/plugins
    depends_on:
      airflow-init:
        condition: service_completed_successfully

volumes:
  postgres-data:
```

```bash
# Start everything
docker compose up -d

# Access the UI
open http://localhost:8080    # admin / admin
```

### Project Structure

```
project/
├── dags/                     # DAG files — Airflow scans this directory
│   ├── user_etl.py
│   ├── reporting_pipeline.py
│   └── data_quality_checks.py
├── plugins/                  # Custom operators, hooks, sensors
│   └── custom_hooks/
│       └── my_api_hook.py
├── tests/
│   └── dags/
│       └── test_user_etl.py
├── docker-compose.yml
└── requirements.txt          # Extra Python packages for DAGs
```

---

## 10. Testing DAGs

### Validate DAG Loads Without Errors

```python
import pytest
from airflow.models import DagBag


def test_dags_load():
    """Verify all DAGs parse without errors."""
    dag_bag = DagBag(dag_folder="dags/", include_examples=False)
    assert len(dag_bag.import_errors) == 0, f"DAG import errors: {dag_bag.import_errors}"


def test_dag_has_expected_tasks():
    dag_bag = DagBag(dag_folder="dags/", include_examples=False)
    dag = dag_bag.get_dag("user_etl")

    assert dag is not None
    task_ids = [t.task_id for t in dag.tasks]
    assert "extract" in task_ids
    assert "transform" in task_ids
    assert "load" in task_ids
```

### Test Task Logic in Isolation

```python
# Test the Python function directly — no Airflow runtime needed
from dags.user_etl import transform

def test_transform_filters_empty_emails():
    raw = [
        {"id": 1, "email": "a@b.com", "full_name": "Alice", "signup_date": "2025-01-01"},
        {"id": 2, "email": "", "full_name": "Bob", "signup_date": "2025-01-02"},
        {"id": 3, "email": None, "full_name": "Charlie", "signup_date": "2025-01-03"},
    ]
    result = transform(raw)
    assert len(result) == 1
    assert result[0]["email"] == "a@b.com"
```

---

## 11. Production Considerations

### Scheduler Tuning

```python
# airflow.cfg or environment variables
AIRFLOW__SCHEDULER__MIN_FILE_PROCESS_INTERVAL = 30   # seconds between DAG file re-parses
AIRFLOW__SCHEDULER__DAG_DIR_LIST_INTERVAL = 60        # seconds between scanning for new DAG files
AIRFLOW__CORE__PARALLELISM = 32                       # max task instances across all DAGs
AIRFLOW__CORE__MAX_ACTIVE_TASKS_PER_DAG = 16          # max concurrent tasks per DAG
AIRFLOW__CORE__MAX_ACTIVE_RUNS_PER_DAG = 3            # max concurrent runs of the same DAG
```

### Alerting

```python
from airflow.decorators import dag, task
from datetime import datetime, timedelta


def on_failure(context):
    """Called when any task in the DAG fails."""
    task_instance = context["task_instance"]
    send_slack_alert(
        f"Task failed: {task_instance.dag_id}.{task_instance.task_id}\n"
        f"Execution date: {context['ds']}\n"
        f"Log: {task_instance.log_url}"
    )


@dag(
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": on_failure,
    },
)
def alerting_example():
    ...
```

### KubernetesExecutor

For production Kubernetes deployments, each task runs as its own pod:

```python
# In airflow.cfg or env
AIRFLOW__CORE__EXECUTOR = KubernetesExecutor
AIRFLOW__KUBERNETES_EXECUTOR__NAMESPACE = airflow
AIRFLOW__KUBERNETES_EXECUTOR__DELETE_WORKER_PODS = True
```

Advantages:
- **Isolation:** each task has its own resources, can use different Docker images
- **Scaling:** Kubernetes handles scheduling and resource allocation
- **No persistent workers:** pods spin up, run the task, and die

---

## 12. Airflow vs Other Orchestrators

| Tool | Best for | Key difference |
|------|----------|----------------|
| **Airflow** | Data/ML pipelines, ETL, batch scheduling | Full platform with UI, scheduler, metadata DB |
| **Prefect** | Modern data pipelines | Hybrid model — orchestrate from cloud, run anywhere |
| **Dagster** | Data assets, software-defined pipelines | Asset-centric (what data exists) vs task-centric (what to do) |
| **Temporal** | Long-running business workflows | Code-first, durable execution, replay on failure |
| **Step Functions** | AWS-native workflows | JSON/YAML state machines, tight AWS integration |
| **Dramatiq** | Web backend background jobs | Task queue, not an orchestrator — no DAGs or scheduling |

---

## 13. Common Pitfalls

### Top-Level Code in DAG Files

Airflow parses DAG files every `min_file_process_interval` seconds. Heavy imports or API calls at the module level slow down the scheduler.

```python
# BAD — runs every time the scheduler parses this file
users = httpx.get("https://api.example.com/users").json()

# GOOD — runs only when the task executes
@task()
def extract():
    import httpx
    return httpx.get("https://api.example.com/users").json()
```

### XCom for Large Data

XCom stores data in the metadata database (PostgreSQL). Pushing large datasets causes:
- Slow serialization/deserialization
- Metadata DB bloat
- Memory issues

```python
# BAD — pushing 100MB DataFrame as XCom
@task()
def extract():
    return huge_dataframe.to_dict()

# GOOD — push a reference, not the data
@task()
def extract() -> str:
    path = "s3://my-bucket/extracts/users_{{ ds }}.parquet"
    huge_dataframe.to_parquet(path)
    return path  # small string in XCom

@task()
def transform(path: str):
    df = pd.read_parquet(path)  # read from S3
```

### Catchup Surprise

```python
@dag(
    schedule="@daily",
    start_date=datetime(2024, 1, 1),  # a year ago
    catchup=True,                      # DEFAULT is True
)
```

With `catchup=True` (the default), Airflow creates a DAG run for every missed schedule interval since `start_date`. Deploying this DAG would immediately trigger ~450 backfill runs.

**Fix:** Set `catchup=False` unless you explicitly want backfilling.
