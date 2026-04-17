# Dramatiq + FastAPI Integration

How to wire Dramatiq into a FastAPI project: project structure, broker setup, endpoints, context propagation, testing, and Docker deployment.

---

## Project Structure

```
myapp/
├── __init__.py
├── main.py              # FastAPI app + lifespan
├── broker.py            # Dramatiq broker setup (imported before actors)
├── tasks.py             # Actor definitions
├── config.py            # Settings (pydantic-settings)
├── middleware.py         # Custom Dramatiq middleware
├── docker-compose.yml   # Redis + API + Worker
├── Dockerfile
└── tests/
    ├── __init__.py
    ├── conftest.py      # Stub broker fixtures
    └── test_tasks.py    # Tests with stub broker
```

The critical rule: **`broker.py` must be imported before any module that defines actors.** Dramatiq needs a configured broker before `@dramatiq.actor` decorators run.

---

## Config Module

```python
# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    redis_url: str = "redis://localhost:6379/0"
    redis_result_url: str = "redis://localhost:6379/1"

settings = Settings()
```

---

## Broker Module

```python
# broker.py
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.results import Results
from dramatiq.results.backends import RedisBackend

from .config import settings

# Create and configure the broker
broker = RedisBroker(url=settings.redis_url)

# Add result backend (optional — only if you need to retrieve task results)
result_backend = RedisBackend(url=settings.redis_result_url)
broker.add_middleware(Results(backend=result_backend))

# Set as the global broker — must happen before any @dramatiq.actor decorators
dramatiq.set_broker(broker)
```

**Why a separate DB for results?** Keeps result keys from colliding with broker queues. Redis databases 0-15 are cheap isolation.

---

## Tasks Module

```python
# tasks.py
import dramatiq
import logging

# This import ensures the broker is configured before actors are defined
from .broker import broker  # noqa: F401 — side effect import

logger = logging.getLogger(__name__)

@dramatiq.actor(store_results=True, max_retries=3, time_limit=300_000)
def process_document(doc_id: str) -> dict:
    """Process a document. Runs in a Dramatiq worker, not in FastAPI."""
    logger.info(f"Processing document {doc_id}")

    document = fetch_document(doc_id)
    result = run_pipeline(document)
    save_result(doc_id, result)

    logger.info(f"Finished processing document {doc_id}")
    return {"doc_id": doc_id, "status": "processed", "pages": result.page_count}


@dramatiq.actor(max_retries=5, min_backoff=2_000)
def send_notification(user_id: str, message: str):
    """Send a push notification to a user."""
    logger.info(f"Sending notification to {user_id}")
    notification_service.send(user_id, message)


@dramatiq.actor(queue_name="low-priority", max_retries=1)
def generate_report(report_id: str, params: dict):
    """Generate a large report — low priority, long running."""
    logger.info(f"Generating report {report_id}")
    build_report(report_id, params)
```

> **Key point:** If you forget the broker import, Dramatiq falls back to the default RabbitMQ broker and your tasks silently go nowhere.

---

## FastAPI Endpoints

```python
# main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import dramatiq.results

# Import tasks — this triggers broker.py, which configures the broker
from .tasks import process_document, send_notification, generate_report


app = FastAPI(title="Document Processing API")


# --- Enqueue a task ---

class ProcessRequest(BaseModel):
    priority: str = "normal"

@app.post("/documents/{doc_id}/process")
async def start_processing(doc_id: str, req: ProcessRequest):
    """Enqueue a document for processing. Returns immediately."""
    message = process_document.send(doc_id)
    return {
        "job_id": message.message_id,
        "status": "queued",
    }


# --- Check task status ---

@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Poll for the result of a previously enqueued task."""
    message = process_document.message_with_options(
        args=(),
        options={"redis_message_id": job_id},
    )
    try:
        result = message.get_result(block=False)
        return {"status": "completed", "result": result}
    except dramatiq.results.ResultMissing:
        return {"status": "pending"}
    except dramatiq.results.ResultTimeout:
        return {"status": "processing"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Fire-and-forget tasks ---

@app.post("/users/{user_id}/notify")
async def notify_user(user_id: str, message: str):
    """Send a notification — no result needed."""
    send_notification.send(user_id, message)
    return {"status": "sent"}


# --- Delayed task ---

@app.post("/reports")
async def create_report(params: dict):
    """Enqueue a report to be generated in 5 minutes."""
    message = generate_report.send_with_options(
        args=("report_001", params),
        delay=300_000,  # 5 minutes
    )
    return {"job_id": message.message_id, "status": "scheduled"}
```

---

## Alternative Result Tracking Pattern

The `message_with_options` approach for result lookup can be fragile. A more robust pattern is to **track job status in your own database**:

```python
# tasks.py
import uuid

@dramatiq.actor(max_retries=3)
def process_document(job_id: str, doc_id: str):
    """Track status in the database, not in Dramatiq's result backend."""
    db.update_job(job_id, status="processing")
    try:
        result = do_processing(doc_id)
        db.update_job(job_id, status="completed", result=result)
    except Exception as e:
        db.update_job(job_id, status="failed", error=str(e))
        raise  # re-raise so Dramatiq's retry logic kicks in


# main.py
@app.post("/documents/{doc_id}/process")
async def start_processing(doc_id: str):
    job_id = str(uuid.uuid4())
    db.create_job(job_id, status="queued")
    process_document.send(job_id, doc_id)
    return {"job_id": job_id}

@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"status": job.status, "result": job.result}
```

> **Why this is better:** You control the schema, can add timestamps, progress tracking, user association, and the data survives Redis flushes.

---

## ContextVar Propagation to Workers

Request context (request ID, user ID, trace ID) does **not** automatically propagate to Dramatiq workers — they are separate processes. Use custom middleware to pass it through message options.

### Define the Context

```python
# context.py
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="unknown")
user_id_var: ContextVar[str] = ContextVar("user_id", default="anonymous")
```

### Custom Middleware

```python
# middleware.py
import dramatiq
from .context import request_id_var, user_id_var

class ContextPropagationMiddleware(dramatiq.Middleware):
    """Propagate context variables from the sender to the worker."""

    def before_process_message(self, broker, message):
        """Restore context from message options (worker side)."""
        request_id = message.options.get("request_id", "unknown")
        user_id = message.options.get("user_id", "anonymous")
        request_id_var.set(request_id)
        user_id_var.set(user_id)

    def after_process_message(self, broker, message, *, result=None, exception=None):
        """Reset context after processing."""
        request_id_var.set("unknown")
        user_id_var.set("anonymous")
```

### Register the Middleware

```python
# broker.py
from .middleware import ContextPropagationMiddleware

broker = RedisBroker(url=settings.redis_url)
broker.add_middleware(ContextPropagationMiddleware())
dramatiq.set_broker(broker)
```

### Send Context with Messages

```python
# main.py
from .context import request_id_var, user_id_var

@app.post("/documents/{doc_id}/process")
async def start_processing(doc_id: str):
    message = process_document.send_with_options(
        args=(doc_id,),
        options={
            "request_id": request_id_var.get(),
            "user_id": user_id_var.get(),
        },
    )
    return {"job_id": message.message_id}
```

### Use Context in Actors

```python
# tasks.py
from .context import request_id_var

@dramatiq.actor
def process_document(doc_id: str):
    logger.info(f"[{request_id_var.get()}] Processing document {doc_id}")
    # The request_id was set by ContextPropagationMiddleware
```

This is the same pattern you'd use for trace IDs, tenant IDs, or any per-request state.

---

## Testing

### Stub Broker

Dramatiq ships a `StubBroker` that keeps everything in-memory — no Redis needed in tests.

```python
# tests/conftest.py
import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker
from dramatiq.results import Results
from dramatiq.results.backends import StubBackend


@pytest.fixture
def stub_broker():
    """Replace the real broker with an in-memory stub."""
    broker = StubBroker()
    broker.add_middleware(Results(backend=StubBackend()))
    broker.emit_after("process_boot")  # trigger middleware setup
    dramatiq.set_broker(broker)
    yield broker
    broker.flush_all()
    broker.close()


@pytest.fixture
def stub_worker(stub_broker):
    """Start a worker that processes messages synchronously."""
    worker = dramatiq.Worker(stub_broker, worker_timeout=100)
    worker.start()
    yield worker
    worker.stop()
```

### Writing Tests

```python
# tests/test_tasks.py
import dramatiq


def test_process_document(stub_broker, stub_worker):
    # Define actor with the stub broker
    @dramatiq.actor(store_results=True)
    def process_document(doc_id: str):
        return {"doc_id": doc_id, "status": "processed"}

    stub_broker.declare_actor(process_document)

    # Enqueue
    msg = process_document.send("doc_123")

    # Wait for the worker to process it
    stub_broker.join(process_document.queue_name)
    stub_worker.join()

    # Check result
    result = msg.get_result(block=False)
    assert result == {"doc_id": "doc_123", "status": "processed"}


def test_task_retry_on_failure(stub_broker, stub_worker):
    call_count = 0

    @dramatiq.actor(max_retries=2)
    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("boom")
        return "ok"

    stub_broker.declare_actor(flaky)

    flaky.send()
    stub_broker.join(flaky.queue_name)
    stub_worker.join()

    assert call_count == 3  # 1 initial + 2 retries
```

`broker.join()` blocks until the queue is drained. `worker.join()` blocks until all threads finish.

### Integration Tests (with Real Redis)

```python
# tests/test_integration.py
import pytest
from httpx import AsyncClient, ASGITransport

from myapp.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_enqueue_document_processing(client):
    response = await client.post("/documents/doc_123/process")
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "queued"
```

---

## Docker Compose

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  api:
    build: .
    command: uvicorn myapp.main:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    environment:
      - REDIS_URL=redis://redis:6379/0
      - REDIS_RESULT_URL=redis://redis:6379/1
    depends_on:
      redis:
        condition: service_healthy

  worker:
    build: .
    command: dramatiq myapp.tasks --processes 2 --threads 4
    environment:
      - REDIS_URL=redis://redis:6379/0
      - REDIS_RESULT_URL=redis://redis:6379/1
    depends_on:
      redis:
        condition: service_healthy
    deploy:
      replicas: 2  # scale workers independently

volumes:
  redis-data:
```

**Same image, different command.** The API runs `uvicorn`, the worker runs `dramatiq`. They share the same codebase and dependencies.

### Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command — overridden by docker-compose for api vs worker
CMD ["uvicorn", "myapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Production Considerations

### 1. Graceful Shutdown

Dramatiq handles `SIGTERM` gracefully by default:
1. Stops consuming new messages
2. Waits for in-progress tasks to finish
3. If tasks don't finish in time, sends `Interrupted` exception

Docker sends `SIGTERM` then waits `stop_grace_period` (default: 10s) before `SIGKILL`.

```yaml
# docker-compose.yml
worker:
  ...
  stop_grace_period: 60s  # give tasks time to finish
```

### 2. Health Checks for Workers

Dramatiq workers don't expose HTTP by default. Options:

```python
# Option A: use the Prometheus middleware (exposes /metrics HTTP endpoint)
from dramatiq.middleware import Prometheus
broker.add_middleware(Prometheus())

# Option B: custom heartbeat file that Kubernetes can check
import dramatiq
import pathlib

class HealthCheckMiddleware(dramatiq.Middleware):
    def after_worker_boot(self, broker, worker):
        pathlib.Path("/tmp/dramatiq-healthy").touch()

    def after_process_message(self, broker, message, *, result=None, exception=None):
        pathlib.Path("/tmp/dramatiq-healthy").touch()

# Note: this file is only refreshed when messages are processed. An idle worker
# won't update it — pair with a liveness probe that checks mtime is recent, or
# add a periodic heartbeat thread. Kubernetes liveness probe (example):
# exec:
#   command: ["test", "-f", "/tmp/dramatiq-healthy"]
```

### 3. Scaling Workers Independently

Workers and API servers scale independently:

```bash
# Scale workers based on queue depth
docker compose up --scale worker=5

# Or in Kubernetes
kubectl scale deployment dramatiq-worker --replicas=10
```

### 4. Separate Workers for Different Queues

```yaml
# docker-compose.yml
worker-high:
  build: .
  command: dramatiq myapp.tasks --queues high-priority --processes 4 --threads 4

worker-default:
  build: .
  command: dramatiq myapp.tasks --queues default low-priority --processes 2 --threads 8
```

### 5. Error Alerting

```python
class AlertingMiddleware(dramatiq.Middleware):
    def after_skip_message(self, broker, message):
        """Message permanently failed after all retries."""
        send_alert(
            channel="task-failures",
            text=f"Task permanently failed: {message.actor_name} "
                 f"(id={message.message_id}, args={message.args})",
        )
```

---

## Mental Model

```
FastAPI (any instance)
    │
    │  task.send(args)  ← instant, non-blocking
    ▼
Redis Broker
    │
    │  message sitting in queue
    ▼
Dramatiq Worker (separate process/container)
    │
    │  worker picks up message, calls function
    ▼
Task executes with retries, time limits, rate limits
    │
    ▼
Result stored in Redis (if store_results=True)
    │
    ▼
FastAPI (any instance) can poll for result
```

> The API and the worker share **code** (same Python package) but not **processes**. The API enqueues; the worker executes. Redis is the glue.
