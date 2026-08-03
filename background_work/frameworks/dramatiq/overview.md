# Dramatiq — Distributed Task Processing

> **Who this is for**: Python engineers evaluating Dramatiq for broker-backed task delivery and synchronous worker execution.

Before reading this, understand **[queue and worker architectures](../../04_queue_and_worker_architectures.md)** and **[the reliability deep dives](../../reliability/README.md)**.

Dramatiq 2.2 is documented in the current [official user guide](https://dramatiq.io/guide.html).

---

## What this framework is

You need broker-backed Python workers but do not want to build serialization, retry, and process lifecycle from scratch. Dramatiq provides actors, Redis/RabbitMQ broker adapters, a process-and-thread worker runtime, middleware, retries, delayed messages, composition, rate limiters, and optional result storage.

| Role | Dramatiq coverage |
|---|---|
| Scheduler | Delayed messages; cron requires another component/add-on |
| Task queue | Yes |
| Worker runtime | Yes |
| Broker abstraction | Redis and RabbitMQ |
| Workflow engine | No |

---

## What this framework is not

Dramatiq is not an authoritative business state machine, a durable human-in-the-loop workflow engine, or a distributed scheduler. Pipelines and groups compose task messages, but the application still owns database/broker atomicity, idempotency, cancellation policy, and audit history.

---

## Installation — quote the extras

Dramatiq requires **Python 3.10 or newer** (3.9 support was dropped in 2.0.0).

```bash
# With Redis broker (most common)
pip install 'dramatiq[redis]'

# With RabbitMQ broker
pip install 'dramatiq[rabbitmq]'

# With watch support for development
pip install 'dramatiq[watch]'

# Only if you add the Prometheus middleware — it is not in the base install
pip install 'dramatiq[prometheus]'
```

The quotes matter: zsh (the default macOS shell) treats `[redis]` as a glob and fails with `zsh: no matches found: dramatiq[redis]` before pip runs at all.

---

## Five concepts carry the whole model

Start with **actor**, **message**, **broker**, **queue**, and **worker**; reach for middleware when behavior must apply across actors.

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

## Every actor needs a broker set before import

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

⚠️ **Forgetting `dramatiq.set_broker()` does not raise.** `get_broker()` builds a default broker on first use — it tries RabbitMQ first and falls back to Redis at `localhost:6379/0` when pika is not installed, which is exactly the case for a `dramatiq[redis]` install. So messages go *somewhere plausible* instead of erroring. The tell: messages are absent from the broker URL you configured and present at the default coordinates. On a developer laptop where both are the same Redis, this can pass every local test and diverge in every deployed environment.

### When to Choose Which

- **Redis** — simpler to operate, good enough for most workloads. Messages live in memory (persistence depends on Redis config). Use if you already run Redis for caching.
- **RabbitMQ** — true message broker with better delivery guarantees, routing, and backpressure. Use for high-throughput or mission-critical messaging.

---

## `.send()` publishes a call; it does not make one

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

> **Key insight**: `.send()` does **not** execute the function. It serializes the call and publishes it to the broker. The function runs later, in a different process and may run more than once.

---

## Retries are on by default, and the defaults are longer than you think

Dramatiq retries failed actors automatically. The default middleware uses **exponential backoff**.

### Default Retry Behavior

```python
@dramatiq.actor  # max_retries=20, min_backoff=15s, max_backoff=7 days
def flaky_task():
    call_unreliable_api()
```

All three defaults together are the fact to internalize: `max_retries` is **20**, `min_backoff` is **15 seconds**, and `max_backoff` is **7 days**. A message that fails persistently therefore keeps retrying for *days* — backing off toward one attempt a week — before it reaches the dead-letter queue. If you want a permanent failure to become visible in minutes, you must say so. Source: [actor options table](https://dramatiq.io/guide.html), checked 2026-08-03.

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

### Never retry a specific exception type

For "these exceptions are permanent," use the `throws` option rather than a predicate:

```python
@dramatiq.actor(throws=(ValueError,))
def parse_payload(raw: str):
    """A ValueError here is bad input, not a transient fault — fail immediately."""
    return json.loads(raw)
```

`throws` exceptions are logged at a lower level and skip retries entirely.

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

⚠️ **Setting `retry_when` makes `max_retries` ignored entirely.** The callable becomes the only bound on retry count — which is why `retries < 5` above is load-bearing, not a convenience. This fails silently and expensively:

```python
# WRONG — retries forever. max_retries is not consulted when retry_when is set.
@dramatiq.actor(max_retries=3, retry_when=lambda r, e: isinstance(e, ConnectionError))
def unbounded():
    ...
```

Source: [actor options table](https://dramatiq.io/guide.html) — "When this is set, `max_retries` is ignored" (checked 2026-08-03).

### Disabling Retries

```python
@dramatiq.actor(max_retries=0)
def no_retry_task():
    """If this fails, it fails. No second chances."""
    ...
```

### What Happens After Max Retries

When all retries are exhausted, the message is **not re-enqueued** onto its original queue. Instead it is moved to a dead-letter queue (the `.XQ` queue — see [Dead Letter Queues](#dead-letter-queues-dlq) below), where it is retained for `dead_message_ttl` (default 7 days) and then dropped.

⚠️ **`after_skip_message` is the wrong hook for this, and it fails by never firing.** It is a natural guess — the docstring in most examples says "all retries exhausted" — but the worker emits `skip_message` from exactly one place: its `except SkipMessage` branch. `Retries` exhausts retries in `after_process_message` by calling `message.fail()`, which never raises `SkipMessage`, so the message goes down the nack path instead and your alert middleware stays silent forever. In the built-in stack, `after_skip_message` fires for an **age-limit** drop (`AgeLimit` calls `message.fail()` and *then* raises `SkipMessage`) and for middleware or an actor raising `SkipMessage` deliberately — not for retry exhaustion. Source: [`worker.py`](https://raw.githubusercontent.com/Bogdanp/dramatiq/v2.2.0/dramatiq/worker.py) and [`middleware/retries.py`](https://raw.githubusercontent.com/Bogdanp/dramatiq/v2.2.0/dramatiq/middleware/retries.py), checked 2026-08-03.

Two hooks do fire. Prefer the first.

**1. The `on_retry_exhausted` actor option** — note the singular `retry` — naming another actor to run when retries run out:

```python
@dramatiq.actor
def alert_ops(message_data: dict, retry_data: dict):
    # message_data is Message.asdict(): queue_name, actor_name, args, kwargs,
    #   options (including "retries" and "traceback"), message_id, message_timestamp
    # retry_data is {"retries": <attempts made>, "max_retries": <configured limit>}
    logger.error(
        "retries_exhausted",
        extra={
            "actor": message_data["actor_name"],
            "message_id": message_data["message_id"],
            "args": message_data["args"],
            "retries": retry_data["retries"],
            "max_retries": retry_data["max_retries"],
            "traceback": message_data["options"].get("traceback"),
        },
    )

@dramatiq.actor(max_retries=3, on_retry_exhausted="alert_ops")
def charge_customer(charge_id: str):
    ...
```

⚠️ **The callback takes two positional arguments, not one.** `Retries` calls `target_actor.send(message.asdict(), {"retries": ..., "max_retries": ...})`. A one-argument `alert_ops(message_data)` therefore fails with `TypeError: alert_ops() takes 1 positional argument but 2 were given` — and it fails *inside the alert actor*, at the exact moment your original actor gave up, so the alert you built to tell you about permanent failures is itself a permanent failure. It will then retry 20 times with its own backoff before dead-lettering.

⚠️ `on_retry_exhausted` is an actor/message option, not a `Retries` constructor argument, and the plural spelling does not exist. Both wrong forms fail loudly, but in different places: `Retries(on_retries_exhausted=...)` raises `TypeError` (the constructor takes only `max_retries`, `min_backoff`, `max_backoff`, `retry_when`), and `@dramatiq.actor(on_retries_exhausted=...)` raises `ValueError: The following actor options are undefined`.

**2. `after_nack` in custom middleware**, when you want one cross-cutting handler instead of a per-actor option. Every rejected message passes through it — retry exhaustion, a `throws`-listed exception, and age-limit drops alike — so you have to read `message.options` to tell them apart:

```python
class DeadLetterMiddleware(dramatiq.Middleware):
    def after_nack(self, broker, message):
        """Called for every message that is rejected instead of acknowledged."""
        actor = broker.get_actor(message.actor_name)
        max_retries = message.options.get(
            "max_retries", actor.options.get("max_retries", 20)
        )
        failed_msg = {
            "actor": message.actor_name,
            "args": message.args,
            "kwargs": message.kwargs,
            "retries": message.options.get("retries", 0),
            "max_retries": max_retries,
            "traceback": message.options.get("traceback"),
        }
        logger.error(f"Dead letter: {failed_msg}")
        db.dead_letters.insert_one(failed_msg)
```

**How you know either one is wired up:** fail an actor deliberately with `max_retries=0` and confirm the worker logs `Retries exceeded for message '<id>'.` at `WARNING`, immediately followed by your alert actor's own log line (or the `dead_letters` row). If you see the `Retries exceeded` line and nothing after it, the hook is not connected — which is precisely the symptom of the `after_skip_message` mistake.

---

## Time limits are best-effort, not guarantees

Set a best-effort ceiling on actor execution.

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

## Results are opt-in, per actor, and not a status store

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

### Storing and retrieving a result

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

## Rate limiters are distributed, and exceeding one is a retry

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

### Hitting the limit re-enqueues, it does not drop

When `acquire()` fails (limit reached), the task raises `dramatiq.rate_limits.RateLimitExceeded`. The default retry middleware catches this and **re-enqueues the message** with a backoff delay. The task is not lost — it will be retried when capacity is available.

---

## Priority sorts within a worker; separate processes isolate

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
# Worker for high-priority only — this is the isolation mechanism
dramatiq myapp.tasks --queues high-priority --processes 4

# Worker for all three queues, consumed CONCURRENTLY — argument order means nothing
dramatiq myapp.tasks --queues high-priority default low-priority
```

**What `priority` actually does**: it is the sort key for a worker's in-memory work queue — sorted by priority, then by queued time as of 2.2.0 — and it applies *only after* messages have already been consumed from the broker. A single worker consumes from every `--queues` entry concurrently, so listing `high-priority` first confers no precedence whatsoever.

The consequence is the part that matters for capacity planning: if one worker process consumes both `high-priority` and `low-priority`, a batch of long low-priority tasks can occupy every worker thread, and urgent work waits behind them regardless of its `priority` number. **To guarantee urgent work is never stuck behind batch work, run separate worker processes per queue.** Source: [Dramatiq guide](https://dramatiq.io/guide.html) — "Actor priority only takes effect when Dramatiq is choosing which message to run. This only happens after messages are consumed from the queues" (checked 2026-08-03).

---

## Composition needs the Results middleware first

⚠️ Everything in this section uses `store_results=True`, and the `Results` middleware is **not** in `default_middleware`. Without the [results setup](#results-are-opt-in-per-actor-and-not-a-status-store) setup above, `@dramatiq.actor(store_results=True)` fails at *import* time with `ValueError: The following actor options are undefined: store_results` — before any message is sent. `Pipelines` itself *is* a default middleware, so results are the only missing piece.

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

### Fan-out then aggregate: use a completion callback, not a nested pipeline

A `group` cannot be an element of a `pipeline`. `pipeline.__init__` accepts only `Message | pipeline` children and calls `child.copy()` on each; `group` defines no `copy()`, so nesting one raises `AttributeError: 'group' object has no attribute 'copy'`.

The supported fan-in is a completion callback on the group, which requires the **opt-in `GroupCallbacks` middleware**:

```python
import dramatiq
from dramatiq.middleware import GroupCallbacks
from dramatiq.rate_limits.backends import RedisBackend as RateLimitBackend

# One-time setup, alongside the broker.
rate_limiter_backend = RateLimitBackend(url="redis://localhost:6379/0")
broker.add_middleware(GroupCallbacks(rate_limiter_backend))
```

```python
# Fan-out then aggregate: process items in parallel, then summarize once.
g = dramatiq.group([process_item.message(item) for item in items])
g.add_completion_callback(aggregate_results.message())
g.run()
```

⚠️ Calling `run()` after `add_completion_callback()` **without** the `GroupCallbacks` middleware registered raises `RuntimeError`. The middleware uses a distributed barrier in the rate-limiter backend to detect the last child finishing, which is why it needs a backend and is not enabled by default.

For genuinely nested graphs — a chain of groups of chains — the third-party [`dramatiq-workflow`](https://pypi.org/project/dramatiq-workflow/) package (0.3.0) builds them on top of these primitives. Source: [`dramatiq/composition.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/composition.py) and the [reference docs](https://dramatiq.io/reference.html), checked 2026-08-03.

---

## `async def` actors work, but do not raise per-process concurrency

Dramatiq supports coroutine actors through the opt-in `AsyncIO` middleware. It is **not** in `default_middleware`:

```python
import dramatiq
from dramatiq.middleware import AsyncIO

broker.add_middleware(AsyncIO())
dramatiq.set_broker(broker)

@dramatiq.actor
async def fetch_report(url: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()
```

The middleware runs **one event-loop thread per worker process** and dispatches coroutine actors onto it.

⚠️ **This does not increase concurrency.** Each worker thread submits its coroutine to the loop and then *blocks on the result*, so maximum in-flight work per process is still the worker-thread count — 8 by default — exactly as it is for synchronous actors. `async def` here buys you the ability to use async libraries inside an actor; it does not buy you the 1,000-concurrent-request profile of a native asyncio worker. If that profile is the requirement, a database- or queue-polling asyncio worker ([task execution models](../../05_task_execution_models.md#4-native-async-io-gives-high-concurrency-with-explicit-backpressure) §4) is the right shape, not Dramatiq.

Source: [reference docs](https://dramatiq.io/reference.html) and [`middleware/asyncio.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/middleware/asyncio.py), checked 2026-08-03.

---

## Middleware is where cross-cutting behavior belongs

Dramatiq's behavior is built on a middleware stack. Every message passes through each middleware's hooks.

### Built-in Middleware

The column that determines whether your code works is **Default?** — an opt-in middleware whose option you use raises `ValueError: The following actor options are undefined` at import time.

| Middleware | What it does | Default? |
|-----------|-------------|:---:|
| `AgeLimit` | Discard messages older than a threshold | ✓ |
| `TimeLimit` | Best-effort interrupt for actors that exceed their time limit | ✓ |
| `ShutdownNotifications` | Notify actors when the worker is shutting down (per-actor opt-in) | ✓ |
| `Callbacks` | Run callback actors on success or failure | ✓ |
| `Pipelines` | Enable pipeline and group support | ✓ |
| `Retries` | Automatic retry with exponential backoff | ✓ |
| `Results` | Store actor return values in a result backend | add explicitly |
| `CurrentMessage` | Access the current message from within an actor | add explicitly |
| `AsyncIO` | Run `async def` actors on a per-process event loop | add explicitly |
| `GroupCallbacks` | Fire a callback when the last member of a group finishes | add explicitly |
| `Prometheus` | Export worker metrics over HTTP | add explicitly (needs `dramatiq[prometheus]`) |

`default_middleware` is exactly `AgeLimit, TimeLimit, ShutdownNotifications, Callbacks, Pipelines, Retries`. Verified against [`middleware/__init__.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/middleware/__init__.py) in 2.2.0, checked 2026-08-03.

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

    def after_nack(self, broker, message):
        """Called when a message is rejected — retries exhausted, `throws`
        exception, or age limit. NOT after_skip_message: that one only fires
        when SkipMessage is raised, which retry exhaustion never does."""
        logger.error(
            f"Message permanently failed: actor={message.actor_name} "
            f"message_id={message.message_id} args={message.args} "
            f"retries={message.options.get('retries', 0)}"
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
# Boot:
before_worker_boot
after_worker_boot

# Per message, on the sending side:
before_enqueue         → about to publish (ALSO fires for every retry)
after_enqueue          → published

# Per message, on the worker side:
before_delay           → message has a delay, about to wait
before_process_message → about to call the actor function
after_process_message  → actor returned or raised
after_ack              → message acknowledged
after_nack             → message rejected: retries exhausted, `throws` match, or age limit
after_skip_message     → SkipMessage was raised (age limit, or raised deliberately)
                         ⚠️ NOT retry exhaustion — see "What Happens After Max Retries"

# Shutdown:
before_consumer_thread_shutdown
before_worker_shutdown
after_worker_shutdown
```

`before_enqueue` firing on retries as well as first sends is the one that catches people out — a counter incremented there measures publish attempts, not distinct jobs.

---

## Worker concurrency is processes x threads

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

**How you know it worked.** At the default verbosity Dramatiq logs exactly **two kinds of INFO line** on boot — one from the main process and one per worker process:

```
[2026-08-03 09:14:02,113] [PID 41233] [MainThread] [dramatiq.MainProcess] [INFO] Dramatiq '2.2.0' is booting up.
[2026-08-03 09:14:02,486] [PID 41236] [MainThread] [dramatiq.WorkerProcess(0)] [INFO] Worker process is ready for action.
[2026-08-03 09:14:02,491] [PID 41237] [MainThread] [dramatiq.WorkerProcess(1)] [INFO] Worker process is ready for action.
```

⚠️ **That is all you get — there is no per-queue INFO line.** Nothing at the default level tells you which queues the worker bound to, so `Worker process is ready for action.` is *not* evidence that the worker is consuming the queue you are sending to. Queue binding is logged at `DEBUG`, one line per queue, and you have to ask for it with `-v`:

```bash
dramatiq myapp.tasks -v
```

```
[...] [dramatiq.worker.Worker] [DEBUG] Adding consumer for queue 'default'.
[...] [dramatiq.worker.Worker] [DEBUG] Adding consumer for delay queue 'default.DQ'.
```

Verified against [`cli.py`](https://raw.githubusercontent.com/Bogdanp/dramatiq/v2.2.0/dramatiq/cli.py) (`LOGFORMAT`, `VERBOSITY = {0: INFO, 1: DEBUG}`) and [`worker.py`](https://raw.githubusercontent.com/Bogdanp/dramatiq/v2.2.0/dramatiq/worker.py) in 2.2.0, checked 2026-08-03.

⚠️ **The most common silent failure is a worker that boots perfectly and consumes nothing**, because it is attached to a different broker or a different queue than the sender. There is no error on either side: `.send()` succeeds, the worker sits idle, and the job never runs. Two checks separate the cases:

```bash
# Did the message actually land? (Redis broker, 'default' queue)
redis-cli hlen dramatiq:default.msgs      # > 0 means messages are queued
redis-cli llen dramatiq:default           # pending message IDs

# What is the worker actually bound to?
redis-cli --scan --pattern 'dramatiq:*'   # every queue the broker knows about
```

If `hlen` is 0, the sender is publishing somewhere else — see the `set_broker` warning above. If it keeps climbing while the worker logs nothing, the worker is consuming a queue no actor sends to; restart it with `-v` and compare the `Adding consumer for queue` lines against the actor's `queue_name`.

### Process and Thread Counts

```
--processes N    Number of worker processes (default: CPU count)
--threads N      Number of threads per process (default: 8)
```

**Guidelines:**
- **I/O-bound tasks** (API calls, database queries): more threads, fewer processes. Example: `--processes 2 --threads 16`
- **CPU-bound tasks** (data processing, image manipulation): more processes, fewer threads. Example: `--processes 8 --threads 1`
- **Mixed workloads**: use separate queues with separate workers tuned to each type

### Shutdown notification is opt-in per actor

When a worker receives `SIGTERM` or `SIGINT`:
1. It stops consuming new messages
2. It waits for in-progress tasks to complete (up to a timeout)
3. Tasks that don't finish in time get an async exception injected by the `ShutdownNotifications` middleware — specifically `dramatiq.middleware.Shutdown`, a subclass of the shared `dramatiq.middleware.Interrupt` base class. The companion case, `dramatiq.middleware.TimeLimitExceeded` (also an `Interrupt`), is what you get when your actor's own `time_limit` fires. Catch `Interrupt` to handle both uniformly.

⚠️ **Step 3 only happens for actors that opt in with `notify_shutdown=True`.** `ShutdownNotifications.should_notify()` defaults to `False`, so an `except Shutdown:` branch on a plain `@dramatiq.actor` is dead code — no exception is ever injected, the checkpoint never runs, and the task is simply killed when the grace period ends. Nothing warns you. The tell: rollouts complete cleanly and your checkpoint log line never appears.

> **Caveat:** these exceptions are injected via `PyThreadState_SetAsyncExc`, which only takes effect the next time the target thread acquires the GIL. A task stuck in a blocking C call or `time.sleep()` will not be interrupted until it returns to Python. Use async I/O or checkpoint loops for reliable interruption.

```python
from dramatiq.middleware import Interrupt, Shutdown, TimeLimitExceeded

@dramatiq.actor(time_limit=600_000, notify_shutdown=True)  # without this, no Shutdown is raised
def long_running_task():
    for chunk in get_data_chunks():
        try:
            process_chunk(chunk)
        except Shutdown:
            # Worker is shutting down — save progress and let it propagate
            save_checkpoint(chunk)
            raise
        except TimeLimitExceeded:
            # Hit our own time_limit — log and decide whether to re-queue
            logger.warning("task exceeded time_limit", extra={"chunk": chunk.id})
            raise
```

Verified against [`middleware/shutdown.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/middleware/shutdown.py) in 2.2.0 (`def __init__(self, notify_shutdown: bool = False)`), checked 2026-08-03.

Operationally, three more things have to line up:

- **Set `terminationGracePeriodSeconds` above your worst-case task duration.** The Kubernetes default of 30 s is usually too short for background workers; past it the container is `SIGKILL`ed regardless of what the actor is doing.
- **Acks happen after the actor returns.** A `SIGKILL`ed worker returns its message to the queue and another worker picks it up — which is why every actor must be idempotent.
- **SIGTERM must reach the worker process.** Dramatiq forwards signals to its own child processes; a custom entrypoint (a shell wrapper without `exec`, or a process supervisor) can swallow the signal so the drain never starts.

---

## Monitoring: what actually works in 2.x

### dramatiq-dashboard is a WSGI app, not a command

⚠️ **`dramatiq-dashboard` ships no console script.** There is nothing to run on the command line — it provides a WSGI middleware you mount in your own app, or a `DashboardApp` you serve yourself:

```python
import dramatiq_dashboard

# Mount the dashboard under /drama in an existing WSGI app.
app = dramatiq_dashboard.make_wsgi_middleware("/drama")(app)
```

⚠️ **It cannot be installed alongside Dramatiq 2.x.** Its last release (0.4.0, 2022-03-17) pins `dramatiq[redis]>=1.6,<2.0` and `redis<5.0`, so `pip install dramatiq-dashboard` in a Dramatiq 2.2 environment either fails to resolve or downgrades Dramatiq out from under you. Treat queue inspection as something you build from the broker keys (below) or from Prometheus metrics until that changes. Source: [PyPI metadata](https://pypi.org/pypi/dramatiq-dashboard/json), checked 2026-08-03.

### Prometheus Metrics

The Prometheus middleware became an **optional dependency in Dramatiq 2.0.0**, and `prometheus.py` imports `prometheus_client` at module scope with no guard — so on a plain `pip install 'dramatiq[redis]'` the import below raises `ImportError`.

```bash
pip install 'dramatiq[prometheus]'
```

```python
from dramatiq.middleware.prometheus import Prometheus

broker.add_middleware(Prometheus())
```

Each worker serves its own metrics endpoint on **`0.0.0.0:9191`** by default (`dramatiq_prom_host` / `dramatiq_prom_port` to change it, `dramatiq_prom_db` for the multiprocess directory). The complete exported set:

| Metric | Type | What it is |
|---|---|---|
| `dramatiq_messages_total` | counter | messages processed, by queue and actor |
| `dramatiq_message_errors_total` | counter | messages that raised |
| `dramatiq_message_retries_total` | counter | retry attempts |
| `dramatiq_message_rejects_total` | counter | messages rejected (dead-lettered) |
| `dramatiq_messages_inprogress` | gauge | currently executing messages |
| `dramatiq_delayed_messages_inprogress` | gauge | messages waiting out a delay |
| `dramatiq_message_duration_milliseconds` | histogram | processing time |

⚠️ Note `dramatiq_messages_inprogress` — **one word, no underscore between `in` and `progress`.** A dashboard or alert built on `dramatiq_messages_in_progress` returns no data and no error, which reads as "no work in flight." Verified against [`middleware/prometheus.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/middleware/prometheus.py) in 2.2.0, checked 2026-08-03.

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

## Patterns worth copying

Tasks may be delivered more than once — a worker can crash after execution but before acknowledgement — so **every actor must be idempotent**. The mechanism is in [Production Patterns → Idempotent Tasks and Deduplication](#idempotent-tasks-and-deduplication) below; a `if already_processed(...)` guard is not it, because the crash window sits between the check and the effect.

### Best-effort fan-out — and why it is not enough for onboarding

The obvious orchestrator is one actor that sends several others:

```python
@dramatiq.actor
def on_user_signup(user_id: str):
    """Best-effort fan-out. Copy this ONLY when every child is losable."""
    send_welcome_email.send(user_id)
    create_default_workspace.send(user_id)
    notify_admin.send(user_id)
    schedule_onboarding_sequence.send(user_id)
```

⚠️ **These are four independent publishes with no atomicity between them.** A crash — or a broker connection drop — after the second `.send()` leaves that user permanently half-onboarded: welcomed, notified, and with no workspace. Dramatiq will redeliver `on_user_signup` itself (it was never acked), which re-sends *all four*, so the two that already succeeded now run twice. There is no state anywhere recording which children were dispatched, so no amount of retrying converges on "each child ran exactly once."

The fix is to make the *set of intents* durable in the same transaction as the signup, and let a publisher drain it:

```python
@dramatiq.actor
def on_user_signup(user_id: str):
    """Durable fan-out: one transaction records every child that must happen."""
    with db.begin() as tx:                       # single atomic write
        tx.execute(
            insert(outbox),
            [
                # (user_id, step) is UNIQUE — a redelivery of on_user_signup
                # re-inserts nothing instead of duplicating the work
                {"user_id": user_id, "step": "welcome_email"},
                {"user_id": user_id, "step": "default_workspace"},
                {"user_id": user_id, "step": "admin_notification"},
                {"user_id": user_id, "step": "onboarding_sequence"},
            ],
        ).on_conflict_do_nothing()
```

A separate publisher polls unpublished outbox rows, `.send()`s each one, and marks it published; each child actor is idempotent on `(user_id, step)`. Now a crash anywhere leaves a recoverable state: either the rows exist and will be published, or the transaction rolled back and the signup did not happen. See [queue and worker architectures §3](../../04_queue_and_worker_architectures.md) for the publisher, and [idempotency and external effects](../../reliability/03_idempotency_and_external_effects.md) for the per-child idempotency.

**Use the best-effort version when** every child is genuinely losable — an analytics ping, a cache warm — and the cost of a missed one is zero. **Use the outbox version whenever a missing child is a support ticket.**

### Delayed Tasks

```python
# Send a reminder 24 hours from now
send_reminder.send_with_options(
    args=(user_id, "Complete your profile!"),
    delay=86_400_000,  # 24 hours in milliseconds
)
```

---

## What to add before production

### Idempotent Tasks and Deduplication

Dramatiq retries failed tasks by default. Without idempotency, a retry after a partial failure double-executes the side effect. Make your actors idempotent by design.

Three strategies:

**1. An operation record written *before* the external call.** The sending side generates a stable key; the actor commits an `in_progress` operation row, makes the provider call outside any open transaction, then commits the result.

The natural first attempt wraps the provider call inside the transaction — and it is wrong in two ways at once:

```python
# WRONG — do not copy
@dramatiq.actor(max_retries=5)
def charge_customer(charge_id: str, amount_cents: int):
    try:
        with db.begin() as tx:
            tx.execute(insert(processed_charges).values(id=charge_id, amount=amount_cents))
            stripe.Charge.create(amount=amount_cents, idempotency_key=charge_id)
    except UniqueViolation:
        return
```

⚠️ **Nothing is committed before the money moves.** The `INSERT` is invisible to every other transaction until `db.begin()` exits, so if the process dies after Stripe accepts the charge but before commit, the row rolls back — the retry sees no record, inserts cleanly, and calls Stripe again. (Stripe's own `idempotency_key` saves you here; a provider without one does not.) Meanwhile the transaction — and the row lock it holds — stays open for the entire duration of a network call to a third party, so a slow Stripe becomes long-held locks and exhausted connection-pool capacity.

```python
@dramatiq.actor(max_retries=5)
def charge_customer(charge_id: str, amount_cents: int):
    request_hash = hash_request(charge_id, amount_cents)

    # 1. Claim the operation in its own committed transaction, BEFORE any effect.
    with db.begin() as tx:
        existing = tx.execute(
            select(operations).where(operations.c.id == charge_id).with_for_update()
        ).one_or_none()
        if existing is not None:
            # Same key, different payload = a caller bug, not a retry. Never
            # return the first charge's result for a different amount.
            if existing.request_hash != request_hash:
                raise IdempotencyKeyConflict(charge_id)
            if existing.state == "SUCCEEDED":
                return existing.result
        else:
            tx.execute(
                insert(operations).values(
                    id=charge_id, request_hash=request_hash, state="IN_PROGRESS"
                )
            )

    # 2. The effect happens with NO transaction open, so a slow provider cannot
    #    hold database locks — and the same stable key is forwarded upstream.
    charge = stripe.Charge.create(amount=amount_cents, idempotency_key=charge_id)

    # 3. Persist the outcome, including the provider's own ID so a crash between
    #    steps 2 and 3 can recover the result instead of re-charging.
    with db.begin() as tx:
        tx.execute(
            update(operations)
            .where(operations.c.id == charge_id)
            .values(state="SUCCEEDED", provider_id=charge.id, result=charge.to_dict())
        )
    return charge.to_dict()
```

The full decision path — in-progress duplicates, same-key/different-hash conflicts, provider-result recovery, and key retention — is in [idempotency and external effects](../../reliability/03_idempotency_and_external_effects.md); this is the Dramatiq-shaped instance of it.

**2. Redis `SET NX` with a short TTL.** Faster but weaker — on Redis failover the dedup state can be lost, and the TTL puts a clock on how long a duplicate is recognised. Acceptable when the cost of occasional double-execution is low; never for money.

**3. Forward an idempotency key to upstream services.** Even if your own dedup fails, the upstream (payment processor or provider) can reject duplicates. Note that this covers only the *provider's* effect — your own database writes still need strategy 1. See [`safe_and_scalable_api_calls/11_idempotency.md`](../../../fundamentals/fastapi/safe_and_scalable_api_calls/11_idempotency.md).

### Dead Letter Queues (DLQ)

Dramatiq has dead-lettering **built in** for both brokers: when a message exhausts its retries (or exceeds its age limit), it is moved to a dead-letter queue, kept for `dead_message_ttl` (default **7 days**, `86400000 * 7` ms), then dropped. You inspect and replay manually within that window.

- With **Redis broker**: the dead-letter state is split across **two** keys. `dramatiq:<queue_name>.XQ` is a sorted set of dead-lettered **message IDs**, scored by dead-letter timestamp; the payloads live in a separate hash, `dramatiq:<queue_name>.XQ.msgs`, keyed by those IDs. So the `default` queue dead-letters to `dramatiq:default.XQ` plus `dramatiq:default.XQ.msgs`. Reading only the sorted set — the obvious thing to do — gets you a list of UUIDs and no payloads. Verified against [`brokers/redis/dispatch.lua`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/brokers/redis/dispatch.lua), checked 2026-08-03.
- With **RabbitMQ broker**: messages exceeding retries are republished to a dedicated `<queue_name>.XQ` queue with the same 7-day TTL. (This is Dramatiq's own mechanism — distinct from configuring a RabbitMQ-level DLX/dead-letter exchange yourself, which you can still do for transport-level failures.)

Operational checklist:

- Alert on DLQ depth — a growing DLQ means something is failing consistently.
- Before replay, check the **failure reason** (logged or in middleware). Replaying a task whose payload is malformed just burns cycles.
- For targeted replay, decode the stored payload and enqueue an explicitly re-targeted copy. Don't auto-replay from the DLQ; it's a manual-recovery tool.

⚠️ `send_with_options(queue_name="original_queue")` **does not re-route the message.** `Actor.message_with_options` hardcodes `queue_name=self.queue_name`, and unrecognised keyword arguments are simply stuffed into `message.options` with no validation on the send path — so the replay silently lands back on the actor's own queue. Nothing raises. Re-target the decoded message instead:

Redrive is an **operator action on a named list of message IDs**, not a loop over the whole DLQ. The loop-over-everything version is the trap: it has no record of what it did, so running it twice — after a timeout, after a colleague ran it, after you scrolled up and re-ran the cell — enqueues every message again.

```python
import redis
from dramatiq import get_broker
from dramatiq.message import Message

r = redis.Redis.from_url("redis://localhost:6379/0")
broker = get_broker()

# Step 1: LIST first. Inspect the failure reason before redriving anything.
for message_id in r.zrange("dramatiq:default.XQ", 0, -1):
    payload = r.hget("dramatiq:default.XQ.msgs", message_id)
    if payload:
        m = Message.decode(payload)
        print(m.message_id, m.actor_name, m.args, m.options.get("traceback", "")[-200:])

# Step 2: redrive an explicitly chosen set, recording the action before enqueueing.
SELECTED_IDS = [b"a3f1...", b"b7c2..."]   # pasted from step 1, reviewed by a human

for message_id in SELECTED_IDS:
    payload = r.hget("dramatiq:default.XQ.msgs", message_id)
    if payload is None:
        continue
    message = Message.decode(payload)

    # Durable record of the redrive, written BEFORE the enqueue. The unique
    # constraint on message_id makes rerunning this script a no-op instead of
    # a second delivery.
    with db.begin() as tx:
        inserted = tx.execute(
            insert(redrives)
            .values(
                message_id=message.message_id,
                actor_name=message.actor_name,
                operator=os.environ["OPERATOR"],
                reason="upstream outage resolved",
            )
            .on_conflict_do_nothing()
        )
        if inserted.rowcount == 0:
            log.info("already_redriven", message_id=message.message_id)
            continue

    # copy() preserves args/kwargs — and therefore the business idempotency key
    # the actor dedups on, so a redrive of already-completed work is a no-op.
    broker.enqueue(message.copy(queue_name="default"))
```

⚠️ **`copy()` keeps the original `message_id` and merges options over the existing ones — including `"retries"`.** A message that dead-lettered with `retries: 20` is re-enqueued still holding that counter, so the `Retries` middleware fails it again on the very first exception. To give the redriven copy a fresh retry budget, override it: `message.copy(queue_name="default", options={"retries": 0})`.

Source: [`dramatiq/actor.py`](https://github.com/Bogdanp/dramatiq/blob/master/dramatiq/actor.py), checked 2026-08-03.

### The broker is an RPC endpoint — secure it like one

⚠️ **Anyone who can write to your broker can invoke any registered actor with any arguments.** Dramatiq's worker looks up `message.actor_name` in its own registry and calls it with the message's `args`/`kwargs` — there is no signature, no authentication on the message, and no allowlist beyond "is this actor registered in this worker." A Redis instance exposed on `0.0.0.0` with no password is therefore a remote-code-invocation surface: `refund_customer`, `delete_account`, and `send_email` are all one `LPUSH` away.

Minimum boundary for any deployment:

- **Private networking.** The broker listens on a private subnet or a Unix socket, never a public interface. For Redis, keep `protected-mode yes` and bind explicitly.
- **Authentication and TLS.** Redis: `requirepass` plus an ACL user, and `rediss://` for the connection URL. RabbitMQ: a per-service user with a password, over `amqps://`. Never ship the `guest:guest` URL from this note's examples — it exists to make the snippet runnable locally.
- **Least privilege per service.** The web app needs *publish* on its queues; workers need *consume*. In Redis this is an ACL with a key-pattern restriction (`~dramatiq:*`); in RabbitMQ it is per-vhost read/write/configure permissions. A compromised web process should not be able to drain or dead-letter the queue.
- **Minimize the payload.** Send IDs, not personal data or secrets — messages sit in the broker in plaintext, land in the `.XQ` hash for 7 days on failure, and get printed in tracebacks and DLQ inspection scripts like the one above. See [queue and worker architectures](../../04_queue_and_worker_architectures.md) for the ID-only convention.

**How you know it worked:** `redis-cli -u redis://<host>:6379 ping` from outside the private network must fail to connect (not return `NOAUTH`, which means the port is reachable), and an unauthenticated `LPUSH dramatiq:default '...'` must be rejected.

### Dramatiq vs Celery — When to Pick Which

Decide first from **broker support**, **execution pool**, and **scheduling needs**; ecosystem and composition matter after those constraints fit.

| Concern | Dramatiq | Celery |
|---------|----------|--------|
| Broker support | Redis, RabbitMQ | Redis, RabbitMQ, SQS, many others |
| Ecosystem maturity | Smaller, newer | Huge, decade-plus of plugins |
| API surface | Focused — `@actor`, `.send()`, middleware | Broader configuration and extension surface |
| Defaults | Automatic retries; process × thread workers | Prefork by default; acknowledgement/prefetch are configurable |
| Gevent support | First-class (via `dramatiq-gevent`) | First-class |
| Scheduled / cron tasks | `periodiq`, or use APScheduler | `celery beat` (built in) |
| Chains / groups / pipelines | Pipelines and groups | Canvas (chains, groups, chords, callbacks) |
| Multi-language | Python only | Python + some node clients |
| Distributed result tracking | Optional per-actor | First-class, with backends |

On scheduling: `periodiq` (0.14.0, 2026-04-16) is the framework-agnostic cron add-on for Dramatiq. Avoid `dramatiq-crontab` for a FastAPI service — it is Django-specific ("Cron style scheduler for asynchronous Dramatiq tasks in Django"). The [APScheduler note](../apscheduler/overview.md) covers the other option: a separate scheduler process whose only job is to `send()` messages. Checked 2026-08-03.

**Pick Celery when:** you need a supported transport or ecosystem integration Dramatiq lacks, Celery Beat, Canvas primitives, or you already operate Celery.

---

## When to use Dramatiq

Use Dramatiq when Redis or RabbitMQ fit, its focused API and middleware cover the task-runtime needs, and the team is prepared to keep business state outside the framework.

---

## When not to use Dramatiq

Do not use Dramatiq when one transactional database job table is operationally simpler, or the requirement is a durable long-lived workflow rather than task delivery.

Being an async codebase is *not* by itself a reason to avoid it — `async def` actors are supported first-class via the `AsyncIO` middleware. The real boundary is narrower: async actors do not raise per-process concurrency above the worker-thread count, so a workload that needs hundreds of concurrent in-flight I/O operations per process wants a native asyncio worker rather than Dramatiq's thread-per-message model.

⚠️ Dramatiq’s [official time-limit documentation](https://dramatiq.io/guide.html#message-time-limits) calls time limits best-effort: they cannot interrupt system calls or code that is not holding the GIL. Provider-level timeouts remain mandatory.

---

## Summary

For a first deployment, focus on **actor**, **send**, **broker**, **worker**, and **retry**. The remaining rows are optional capabilities.

| Concept | What to remember |
|---------|-----------------|
| `@dramatiq.actor` | Turns a function into a sendable task |
| `.send()` | Fire-and-forget — puts message on the queue |
| `.send_with_options()` | Send with delay, priority, custom retries |
| Broker | Redis (simple) or RabbitMQ (durable) |
| Results | Optional — enable `store_results=True` per actor |
| Retries | On by default — 20 attempts, 15 s to 7 days of backoff |
| Rate limits | Distributed via Redis backend |
| Pipelines | Sequential chaining of actors |
| Groups | Parallel fan-out; fan-in needs `GroupCallbacks` |
| Workers | `dramatiq module_name` — processes + threads |
| Time limits | Best-effort interruption for actors that exceed the limit |
| Middleware | Pluggable hooks; `Results`, `AsyncIO`, `Prometheus` are opt-in |

> **Mental model:** Dramatiq is a mailroom. Your app writes letters (messages) and drops them in mailboxes (queues). Workers pick up letters and do the work. If a letter fails, it goes back in the mailbox for another try.

---

**Next**: [Dramatiq + FastAPI Integration](fastapi_integration.md)
