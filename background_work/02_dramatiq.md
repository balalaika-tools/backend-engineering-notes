# Dramatiq — Distributed Task Processing

Deep dive into Dramatiq: actors, brokers, retries, rate limits, pipelines, and monitoring.

---

## Installation

```bash
# With Redis broker (most common)
pip install dramatiq[redis]

# With RabbitMQ broker
pip install dramatiq[rabbitmq]

# With watch support for development
pip install dramatiq[watch]
```

---

## Core Concepts

| Concept | What it is |
|---------|-----------|
| **Actor** | A function decorated with `@dramatiq.actor` — the unit of work |
| **Message** | A serialized function call (name + args + options) sent to the broker |
| **Queue** | A named channel that messages are routed to (default: `"default"`) |
| **Broker** | The message transport layer (Redis or RabbitMQ) |
| **Worker** | A process that consumes messages from queues and executes actors |
| **Middleware** | Pluggable hooks that run before/after message processing |

### How it flows

```
Your code calls  actor.send(*args)
       │
       ▼
Message is serialized (JSON) and published to the broker
       │
       ▼
A worker process picks up the message from the queue
       │
       ▼
The worker deserializes the message and calls the actor function
       │
       ▼
On success: message is acknowledged and removed from the queue
On failure: middleware decides whether to retry or dead-letter
```

---

## Broker Setup

### Redis Broker

```python
import dramatiq
from dramatiq.brokers.redis import RedisBroker

# Simple
broker = RedisBroker(url="redis://localhost:6379/0")
dramatiq.set_broker(broker)

# With explicit middleware (overrides defaults — usually you extend rather than replace)
from dramatiq.middleware import default_middleware

broker = RedisBroker(
    url="redis://localhost:6379/0",
    middleware=[m() for m in default_middleware],
)
dramatiq.set_broker(broker)
```

### RabbitMQ Broker

```python
from dramatiq.brokers.rabbitmq import RabbitmqBroker

broker = RabbitmqBroker(url="amqp://guest:guest@localhost:5672/")
dramatiq.set_broker(broker)
```

### When to Choose Which

- **Redis** — simpler to operate, good enough for most workloads. Messages live in memory (persistence depends on Redis config). Use if you already run Redis for caching.
- **RabbitMQ** — true message broker with better delivery guarantees, routing, and backpressure. Use for high-throughput or mission-critical messaging.

---

## Defining and Calling Actors

### Basic Actor

```python
import dramatiq

@dramatiq.actor
def send_email(to: str, subject: str, body: str):
    """Send an email. This runs in a worker process, not in your API."""
    smtp_client = get_smtp_client()
    smtp_client.send(to=to, subject=subject, body=body)
    print(f"Email sent to {to}")
```

### Enqueuing Messages

```python
# Fire and forget — returns immediately
send_email.send("user@example.com", "Welcome!", "Thanks for signing up.")

# With keyword arguments
send_email.send(to="user@example.com", subject="Welcome!", body="Thanks.")

# With a delay (in milliseconds)
send_email.send_with_options(
    args=("user@example.com", "Reminder", "Don't forget!"),
    delay=60_000,  # 60 seconds from now
)
```

### What `.send()` Returns

```python
message = send_email.send("user@example.com", "Hello", "World")

print(message.message_id)   # UUID — unique identifier for this message
print(message.queue_name)   # "default"
print(message.actor_name)   # "send_email"
print(message.args)         # ("user@example.com", "Hello", "World")
print(message.options)      # {"redis_message_id": ...}
```

> **Key insight:** `.send()` does **not** execute the function. It serializes the call and publishes it to the broker. The function runs later, in a different process.

---

## Retries

Dramatiq retries failed actors automatically. The default middleware uses **exponential backoff**.

### Default Retry Behavior

```python
@dramatiq.actor  # max_retries defaults to the Retries middleware setting (20)
def flaky_task():
    call_unreliable_api()
```

### Custom Retry Settings

```python
@dramatiq.actor(
    max_retries=3,           # give up after 3 retries (4 total attempts)
    min_backoff=1_000,       # minimum 1 second between retries
    max_backoff=60_000,      # maximum 60 seconds between retries
)
def unreliable_task():
    response = requests.get("https://flaky-api.com/data")
    response.raise_for_status()
```

### Conditional Retries

```python
@dramatiq.actor(
    retry_when=lambda retries, exc: isinstance(exc, ConnectionError) and retries < 5
)
def api_call():
    """Only retry on ConnectionError, and only up to 5 times."""
    response = requests.post("https://api.example.com/webhook")
    response.raise_for_status()
```

### Disabling Retries

```python
@dramatiq.actor(max_retries=0)
def no_retry_task():
    """If this fails, it fails. No second chances."""
    ...
```

### What Happens After Max Retries

When all retries are exhausted, the message is **not re-enqueued**. By default it is simply logged and discarded. To capture these, you can:

1. Override `after_skip_message` in custom middleware
2. Use a dead-letter queue pattern (send failed messages to a separate queue)

```python
class DeadLetterMiddleware(dramatiq.Middleware):
    def after_skip_message(self, broker, message):
        """Called when a message is skipped (all retries exhausted)."""
        failed_msg = {
            "actor": message.actor_name,
            "args": message.args,
            "kwargs": message.kwargs,
            "options": message.options,
        }
        logger.error(f"Dead letter: {failed_msg}")
        db.dead_letters.insert_one(failed_msg)
```

---

## Time Limits

Protect against tasks that hang forever.

```python
@dramatiq.actor(time_limit=300_000)  # 5 minutes max (in milliseconds)
def long_task():
    """If this takes more than 5 minutes, the worker will interrupt it."""
    process_large_dataset()
```

When a time limit is exceeded:
1. The worker raises `dramatiq.middleware.time_limit.TimeLimitExceeded` in the actor's thread
2. Normal retry logic applies (the task can be retried if retries remain)

```python
import dramatiq
from dramatiq.middleware import TimeLimitExceeded

@dramatiq.actor(time_limit=60_000, max_retries=2)
def bounded_task():
    try:
        do_work()
    except TimeLimitExceeded:
        # Clean up partial work if needed
        rollback_partial_changes()
        raise  # re-raise so retry logic kicks in
```

Default time limit is **600,000 ms (10 minutes)**.

---

## Result Backend

By default, Dramatiq is fire-and-forget. To retrieve return values, enable the **Results middleware**.

### Setup

```python
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.results import Results
from dramatiq.results.backends import RedisBackend

broker = RedisBroker(url="redis://localhost:6379/0")
result_backend = RedisBackend(url="redis://localhost:6379/1")  # separate DB
broker.add_middleware(Results(backend=result_backend))
dramatiq.set_broker(broker)
```

### Storing and Retrieving Results

```python
@dramatiq.actor(store_results=True)
def add(a: int, b: int) -> int:
    return a + b

# Enqueue
message = add.send(3, 4)

# Block until result is ready (with timeout)
result = message.get_result(block=True, timeout=10_000)  # 10s timeout
print(result)  # 7

# Non-blocking check
from dramatiq.results import ResultMissing

try:
    result = message.get_result(block=False)
    print(f"Done: {result}")
except ResultMissing:
    print("Still processing...")
```

### Result TTL

```python
@dramatiq.actor(store_results=True, result_ttl=3_600_000)  # 1 hour
def expensive_computation(data):
    return process(data)
```

> **Tip:** Only enable `store_results=True` on actors where you actually need the return value. Storing results for every task wastes Redis memory.

---

## Rate Limiting

Control concurrency against external APIs or shared resources.

### Concurrent Rate Limiter

Limits how many instances of a task can run **at the same time**.

```python
import dramatiq
from dramatiq.rate_limits import ConcurrentRateLimiter
from dramatiq.rate_limits.backends import RedisBackend as RateLimitBackend

rate_limit_backend = RateLimitBackend(url="redis://localhost:6379/0")

# At most 10 concurrent calls to this API
API_LIMITER = ConcurrentRateLimiter(
    rate_limit_backend,
    "external-api-limiter",
    limit=10,
)

@dramatiq.actor
def call_external_api(payload: dict):
    with API_LIMITER.acquire():
        response = requests.post("https://api.example.com/process", json=payload)
        response.raise_for_status()
        return response.json()
```

### Window Rate Limiter

Limits how many times a task can run **within a time window**.

```python
from dramatiq.rate_limits import WindowRateLimiter

# At most 100 calls per 60 seconds
WINDOW_LIMITER = WindowRateLimiter(
    rate_limit_backend,
    "api-window-limiter",
    limit=100,
    window=60_000,  # 60 seconds
)

@dramatiq.actor
def rate_limited_task():
    with WINDOW_LIMITER.acquire():
        call_external_service()
```

### What Happens When the Limit is Hit

When `acquire()` fails (limit reached), the task raises `dramatiq.rate_limits.RateLimitExceeded`. The default retry middleware catches this and **re-enqueues the message** with a backoff delay. The task is not lost — it will be retried when capacity is available.

---

## Priority Queues

Route tasks to different queues with different priorities.

```python
@dramatiq.actor(queue_name="high-priority", priority=0)  # lower number = higher priority
def urgent_notification(user_id: str, message: str):
    push_notification(user_id, message)

@dramatiq.actor(queue_name="default")
def normal_task(data: dict):
    process(data)

@dramatiq.actor(queue_name="low-priority", priority=100)
def batch_report(report_id: str):
    generate_large_report(report_id)
```

Start workers that listen to specific queues:

```bash
# Worker for high-priority only
dramatiq myapp.tasks --queues high-priority --processes 4

# Worker for all queues (processes high-priority first)
dramatiq myapp.tasks --queues high-priority default low-priority
```

> **Note:** Priority within a queue is advisory. Priority across queues is controlled by which queues a worker listens to and in what order.

---

## Groups and Pipelines

### Pipelines (Sequential)

Chain actors together — each step's result feeds into the next.

```python
import dramatiq

@dramatiq.actor(store_results=True)
def download(url: str) -> bytes:
    return requests.get(url).content

@dramatiq.actor(store_results=True)
def parse(raw: bytes) -> dict:
    return json.loads(raw)

@dramatiq.actor(store_results=True)
def store(data: dict) -> str:
    db.insert(data)
    return "stored"

# Build and run a pipeline
pipe = dramatiq.pipeline([
    download.message("https://api.example.com/data"),
    parse.message(),    # receives download's return value
    store.message(),    # receives parse's return value
])
pipe.run()

# Get the final result
final = pipe.get_result(block=True, timeout=30_000)
print(final)  # "stored"
```

### Groups (Parallel)

Execute multiple tasks in parallel and wait for all to complete.

```python
import dramatiq

@dramatiq.actor(store_results=True)
def resize_image(image_id: str, size: str) -> str:
    # resize and save
    return f"{image_id}_{size}.jpg"

# Process all sizes in parallel
group = dramatiq.group([
    resize_image.message("img_123", "thumbnail"),
    resize_image.message("img_123", "medium"),
    resize_image.message("img_123", "large"),
])
group.run()

# Wait for all results
results = group.get_results(block=True, timeout=60_000)
print(results)  # ["img_123_thumbnail.jpg", "img_123_medium.jpg", "img_123_large.jpg"]
```

### Combining Groups and Pipelines

```python
# Fan-out then aggregate: process items in parallel, then summarize
pipe = dramatiq.pipeline([
    dramatiq.group([
        process_item.message(item) for item in items
    ]),
    aggregate_results.message(),
])
pipe.run()
```

---

## Middleware System

Dramatiq's behavior is built on a middleware stack. Every message passes through each middleware's hooks.

### Built-in Middleware

| Middleware | What it does |
|-----------|-------------|
| `Retries` | Automatic retry with exponential backoff |
| `TimeLimit` | Kill actors that exceed their time limit |
| `ShutdownNotifications` | Notify actors when the worker is shutting down |
| `Callbacks` | Run callback actors on success or failure |
| `Pipelines` | Enable pipeline and group support |
| `Results` | Store actor return values in a result backend |
| `AgeLimit` | Discard messages older than a threshold |
| `CurrentMessage` | Access the current message from within an actor |

### Writing Custom Middleware

```python
import dramatiq
import time
import logging

logger = logging.getLogger(__name__)

class TimingMiddleware(dramatiq.Middleware):
    """Log how long each actor takes to execute."""

    def before_process_message(self, broker, message):
        message.options["start_time"] = time.monotonic()

    def after_process_message(self, broker, message, *, result=None, exception=None):
        start = message.options.get("start_time")
        if start is not None:
            duration = time.monotonic() - start
            status = "failed" if exception else "succeeded"
            logger.info(
                f"Actor {message.actor_name} {status} in {duration:.3f}s "
                f"(message_id={message.message_id})"
            )

    def after_skip_message(self, broker, message):
        """Called when a message is skipped (e.g., max retries exceeded)."""
        logger.error(
            f"Message permanently failed: actor={message.actor_name} "
            f"message_id={message.message_id} args={message.args}"
        )
```

Register it:

```python
broker = RedisBroker(url="redis://localhost:6379/0")
broker.add_middleware(TimingMiddleware())
dramatiq.set_broker(broker)
```

### Middleware Hooks (Lifecycle)

```
before_consumer_thread_shutdown
before_worker_boot
after_worker_boot

# Per message:
before_delay           → message has a delay, about to wait
before_process_message → about to call the actor function
after_process_message  → actor returned or raised
after_skip_message     → message will not be retried (max retries, age limit, etc.)

before_worker_shutdown
after_worker_shutdown
```

---

## Worker CLI

### Starting Workers

```bash
# Basic — auto-detects actors in the module
dramatiq myapp.tasks

# Multiple modules
dramatiq myapp.tasks myapp.other_tasks

# Control concurrency
dramatiq myapp.tasks --processes 4 --threads 8
# Total concurrency = 4 processes x 8 threads = 32 concurrent tasks

# Listen to specific queues
dramatiq myapp.tasks --queues high-priority default

# Development mode — auto-reload on file changes
dramatiq myapp.tasks --watch .
```

### Process and Thread Counts

```
--processes N    Number of worker processes (default: CPU count)
--threads N      Number of threads per process (default: 8)
```

**Guidelines:**
- **I/O-bound tasks** (API calls, database queries): more threads, fewer processes. Example: `--processes 2 --threads 16`
- **CPU-bound tasks** (data processing, image manipulation): more processes, fewer threads. Example: `--processes 8 --threads 1`
- **Mixed workloads**: use separate queues with separate workers tuned to each type

### Graceful Shutdown

When a worker receives `SIGTERM` or `SIGINT`:
1. It stops consuming new messages
2. It waits for in-progress tasks to complete (up to a timeout)
3. Tasks that don't finish in time receive `ShutdownNotifications` (the `Interrupted` exception)

```python
from dramatiq.middleware import Interrupted

@dramatiq.actor(time_limit=600_000)
def long_running_task():
    for chunk in get_data_chunks():
        try:
            process_chunk(chunk)
        except Interrupted:
            # Worker is shutting down — save progress
            save_checkpoint(chunk)
            raise
```

---

## Monitoring

### dramatiq-dashboard

A simple web UI for inspecting queues and messages.

```bash
pip install dramatiq-dashboard
dramatiq-dashboard redis://localhost:6379/0
# Opens a web UI at http://localhost:8080
```

### Prometheus Metrics

```python
from dramatiq.middleware import Prometheus

broker.add_middleware(Prometheus())
```

Exposes metrics at each worker's HTTP endpoint:
- `dramatiq_messages_total` — messages processed (by actor, queue, status)
- `dramatiq_message_duration_milliseconds` — processing time histogram
- `dramatiq_messages_in_progress` — currently executing messages

### Structured Logging

Dramatiq logs all message processing at the `INFO` level by default. For structured logging:

```python
import logging
import json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        })

# Apply to dramatiq's logger
dramatiq_logger = logging.getLogger("dramatiq")
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
dramatiq_logger.handlers = [handler]
```

---

## Common Patterns

### Idempotent Actors

Tasks may be delivered more than once (e.g., worker crashes after execution but before acknowledgment). **Always design actors to be idempotent.**

```python
@dramatiq.actor(max_retries=3)
def charge_customer(payment_id: str):
    """Use payment_id as idempotency key — safe to call multiple times."""
    if payment_already_processed(payment_id):
        return  # no-op

    process_payment(payment_id)
    mark_payment_processed(payment_id)
```

### Actor Composition

```python
@dramatiq.actor
def on_user_signup(user_id: str):
    """Orchestrator — enqueues multiple tasks."""
    send_welcome_email.send(user_id)
    create_default_workspace.send(user_id)
    notify_admin.send(user_id)
    schedule_onboarding_sequence.send(user_id)
```

### Delayed Tasks

```python
# Send a reminder 24 hours from now
send_reminder.send_with_options(
    args=(user_id, "Complete your profile!"),
    delay=86_400_000,  # 24 hours in milliseconds
)
```

---

## Summary

| Concept | What to remember |
|---------|-----------------|
| `@dramatiq.actor` | Turns a function into a sendable task |
| `.send()` | Fire-and-forget — puts message on the queue |
| `.send_with_options()` | Send with delay, priority, custom retries |
| Broker | Redis (simple) or RabbitMQ (durable) |
| Results | Optional — enable `store_results=True` per actor |
| Retries | Automatic with exponential backoff by default |
| Rate limits | Distributed via Redis backend |
| Pipelines | Sequential chaining of actors |
| Groups | Parallel fan-out of actors |
| Workers | `dramatiq module_name` — processes + threads |
| Time limits | Kill actors that run too long |
| Middleware | Pluggable hooks for cross-cutting concerns |

> **Mental model:** Dramatiq is a mailroom. Your app writes letters (messages) and drops them in mailboxes (queues). Workers pick up letters and do the work. If a letter fails, it goes back in the mailbox for another try.
