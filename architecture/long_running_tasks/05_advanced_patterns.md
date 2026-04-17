# Advanced Patterns: Sagas, Outbox, Delivery Semantics, Progress, Tracing

> Patterns that come up once you're past "send a message, hope it works." Each of these addresses a specific failure mode you will eventually hit in production and can't fix with retries alone.

---

## 1. The Saga Pattern — Multi-Step Transactions Without a Distributed Commit

A **saga** is a sequence of local transactions across multiple services, each with a compensating action that undoes it. Instead of a single distributed transaction (which requires 2PC / XA, generally impractical), you do the work as a chain of local commits and rollbacks.

### The problem

```
Place order:
  1. Reserve inventory      (Inventory service)
  2. Charge payment         (Payment service)
  3. Create shipment        (Shipping service)
  4. Send confirmation      (Notification service)
```

Step 3 fails. Steps 1 and 2 already committed to their own databases. You need to **unwind** — release the inventory, refund the payment. Each service must know how to undo its own step.

### Two flavors

**Choreography** — services listen for events and react. Step 1 emits `InventoryReserved`; payment service reacts, emits `PaymentCharged`; shipping reacts; etc. On failure, services emit compensating events (`InventoryReleased`, `PaymentRefunded`) that other services consume.

- Pros: loose coupling, no central controller.
- Cons: flow is implicit — you can't read the saga in one place. Hard to reason about "what is the state of this order right now" without event archaeology.

**Orchestration** — a saga orchestrator drives the flow explicitly. It knows the step order and, on failure, walks backwards calling compensating actions.

- Pros: the saga is visible as code, one place to read.
- Cons: the orchestrator is coupled to every participant. Good frameworks (Temporal, AWS Step Functions, Uber Cadence) make this robust; hand-rolling one is painful.

### Compensating actions are not rollbacks

- A local transaction can be truly rolled back.
- A compensating action is a **new transaction** that undoes the effect of a committed one. It may not be a perfect inverse (you can refund a payment, but you can't un-send an email).
- Compensations must themselves be idempotent — the compensator might be retried.
- Some actions are un-compensatable (sending physical mail). For those, the saga must make them the *last* step, so nothing earlier can fail after them.

### When to reach for a saga framework

If your workflow is:
- Long-running (minutes to days)
- Multi-service
- Needs durable state if the orchestrator crashes mid-flight

…use **Temporal** or **AWS Step Functions**. Don't build your own orchestrator unless you enjoy reimplementing distributed systems. Both frameworks handle durability, retries, versioning, and human intervention (manual approval steps).

---

## 2. The Outbox Pattern — Atomic "DB Write + Event Emission"

The classic bug:

```python
# ❌ Dual-write problem
db.orders.insert(order)
event_bus.publish("OrderCreated", order)
```

These are two separate writes to two separate systems. Either can fail after the other succeeded. You can crash after the DB commit but before publishing — now the DB has an order with no downstream reaction. Or the publish succeeds but the DB commit rolls back — downstream services react to an order that doesn't exist.

### The outbox

Write the event into the **same database transaction** as the business data, to an `outbox` table. A separate process reads the outbox and publishes events. Now the DB commit is the single source of truth — either the order exists and the event is in the outbox, or neither.

```sql
BEGIN;
INSERT INTO orders (id, total, ...) VALUES (...);
INSERT INTO outbox (id, aggregate_id, event_type, payload, status)
     VALUES (gen_random_uuid(), order_id, 'OrderCreated', '{...}', 'pending');
COMMIT;
```

A publisher process / worker:

```python
async def outbox_publisher():
    while True:
        # Read batch of unpublished events
        events = await db.fetch(
            "SELECT id, event_type, payload FROM outbox "
            "WHERE status = 'pending' ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED"
        )
        for e in events:
            try:
                await broker.publish(e["event_type"], e["payload"])
                await db.execute(
                    "UPDATE outbox SET status='published', published_at=now() WHERE id=$1",
                    e["id"],
                )
            except Exception:
                log.exception("publish_failed", event_id=e["id"])
                # leave as pending; next pass will retry

        if not events:
            await asyncio.sleep(1)
```

Key details:
- `FOR UPDATE SKIP LOCKED` lets multiple publishers run without fighting for the same rows.
- **At-least-once delivery** — the publisher can crash between publishing and updating status, causing a republish. Consumers must be idempotent.
- **Ordering** — within one aggregate (one order), events stay ordered. Across aggregates, ordering is a design choice.
- **Garbage collection** — published events shouldn't live forever. A separate job deletes rows older than N days, or uses table partitioning by date.

### Outbox + CDC (Change Data Capture)

An increasingly common variant: instead of a polling publisher, use Debezium / Postgres logical decoding to stream the outbox table directly to Kafka. Same atomicity guarantee; much higher throughput and no polling loop to tune.

---

## 3. Delivery Semantics: At-Most-Once vs At-Least-Once vs Exactly-Once

Distributed systems offer three nominal guarantees. Only two are real.

| Semantics | What it means | How you get it | Cost |
|-----------|---------------|----------------|------|
| **At-most-once** | Each message delivered ≤ 1 time | Fire-and-forget; ack before processing | Messages can be lost on any failure |
| **At-least-once** | Each message delivered ≥ 1 time | Ack after processing; retry on failure | Messages can be processed multiple times |
| **"Exactly-once"** | Each message delivered exactly 1 time | Doesn't exist end-to-end without additional mechanisms | — |

### Why exactly-once is a myth (end-to-end)

The fundamental problem: between "I did the work" and "I told the broker I did the work," you can crash. After recovery, you don't know whether the broker saw your ack. You either redo the work (and risk re-executing the side effect) or skip it (and risk losing it). There's no way to know which happened without idempotency on the receiver side.

What vendors mean by "exactly-once":

- **Kafka "exactly-once semantics"** — true *within Kafka*: producers write to Kafka exactly once, consumers read exactly once. The moment the consumer's output leaves Kafka (to a database, to an HTTP call), you're back to at-least-once unless the output is idempotent.
- **SQS FIFO with deduplication** — dedup window of 5 minutes. Within that window, duplicates are dropped. Beyond it, duplicates slip through.

### The practical rule

**Target at-least-once delivery + idempotent consumers = effectively exactly-once.**

The idempotent consumer is the key. Your handler must survive being called with the same message twice. Patterns:

- **Dedup table**: record processed message IDs; reject duplicates. Matches the idempotency-key pattern in [`Safe_and_Scalable_API_calls/11_idempotency.md`](../../fundamentals/fastapi/Safe_and_Scalable_API_calls/11_idempotency.md).
- **Upsert, not insert**: if the outcome is deterministic given the input, use `INSERT ... ON CONFLICT DO UPDATE` or equivalent.
- **Read-before-write**: for operations where idempotency is naturally expressible ("set balance to X" is idempotent; "increment balance by X" is not), express them in the idempotent form.

---

## 4. Progress Reporting, Cancellation, and Resumability

Long-running tasks need more than "started / finished / failed." Users want to see progress bars, cancel mid-flight, and have the task survive a worker restart without starting from scratch.

### Progress reporting

Write progress to a shared store (Redis, DB) keyed by task ID:

```python
@dramatiq.actor
def process_large_file(task_id: str, file_path: str):
    total = count_records(file_path)
    for i, record in enumerate(iter_records(file_path)):
        process(record)
        if i % 100 == 0:  # don't hammer Redis
            redis.hset(f"task:{task_id}", mapping={
                "status": "running",
                "progress": i,
                "total": total,
                "updated_at": time.time(),
            })
    redis.hset(f"task:{task_id}", "status", "done")
```

Client polls or subscribes (Redis pub/sub, WebSocket) for updates.

### Cancellation

Cooperative. The worker must check a cancellation flag; there's no portable way to preempt a running Python function safely. (The Dramatiq async-exception trick from §Time Limits applies but is best-effort.)

```python
@dramatiq.actor
def cancellable_work(task_id: str):
    for chunk in chunks:
        if redis.get(f"cancel:{task_id}"):
            log.info("task_cancelled", task_id=task_id)
            redis.hset(f"task:{task_id}", "status", "cancelled")
            return
        process(chunk)
```

User clicks "cancel" → API sets `cancel:{task_id}` → worker sees it on the next loop iteration and exits cleanly.

### Resumability

If the worker crashes mid-task, you don't want to start over on a 2-hour batch job. Two approaches:

**Checkpoint-based.** Periodically persist progress + any derived state. On restart, load the checkpoint and resume.

```python
@dramatiq.actor
def batch_job(task_id: str, input_ids: list[int]):
    start = redis.hget(f"task:{task_id}", "resume_from")
    start = int(start) if start else 0

    for i in range(start, len(input_ids)):
        result = process(input_ids[i])
        save_result(input_ids[i], result)
        if i % 100 == 0:
            redis.hset(f"task:{task_id}", "resume_from", i + 1)
```

**Sub-task decomposition.** Break the job into many small messages, each idempotent. The broker's at-least-once delivery + your idempotent consumer makes resume automatic — any sub-task the worker didn't ack gets redelivered. This is usually the better pattern when the work decomposes cleanly.

---

## 5. Distributed Tracing — Seeing Across Service Boundaries

When a request fans out through API → queue → worker → upstream → database, logs alone don't tell you the shape of the request. Distributed tracing (OpenTelemetry) assigns a **trace ID** to the initial request and a **span ID** to each segment of work; every segment references its parent. You end up with a tree of spans you can render as a timeline.

### What to instrument

The minimum useful instrumentation for a long-running-task architecture:

1. **HTTP ingress** — FastAPI's `opentelemetry-instrumentation-fastapi` auto-instruments every request. The current trace context goes into `request.state.
2. **Broker enqueue / dequeue** — inject the trace context into the message headers on send; extract on receive. OpenTelemetry's semantic conventions define `messaging.*` attributes for this.
3. **HTTP client calls** — `opentelemetry-instrumentation-httpx` auto-propagates context via `traceparent` headers.
4. **Database calls** — `opentelemetry-instrumentation-sqlalchemy` adds spans for each query.

### Cross-service propagation

The trace context travels in the **`traceparent`** HTTP header (W3C standard). When your FastAPI service calls an HTTPX client, the instrumented client automatically adds this header. When the upstream is also instrumented, it picks up the header and its spans become children of yours.

For the **message broker leg** (API sends message → worker processes), you must manually thread the context:

```python
# API: inject trace context into message
from opentelemetry import propagate

def send_task(payload, actor):
    headers = {}
    propagate.inject(headers)                  # writes traceparent + tracestate
    actor.send_with_options(args=(payload,), kwargs={"_tracectx": headers})

# Worker: extract and continue the trace
@dramatiq.actor
def my_actor(payload, _tracectx=None):
    ctx = propagate.extract(_tracectx or {})
    with tracer.start_as_current_span("process_task", context=ctx):
        do_work(payload)
```

Now one Jaeger / Honeycomb / Tempo timeline shows: HTTP request → enqueue → dequeue (possibly minutes later) → worker processing → upstream API calls → DB queries, as a single connected trace.

### Sampling

Tracing every request is expensive. Sample aggressively — 1% is a reasonable starting point. Most tracers let you configure **tail-based sampling**: keep all traces that contain an error or exceed a latency threshold, sample the rest. That gives you the traces you actually need to debug without paying for the 99% of boring ones.

---

## See also

- [02_worker_patterns.md](./02_worker_patterns.md) — worker patterns that consume these.
- [04_infrastructure.md](./04_infrastructure.md) — the Redis / SQS / Kafka pieces the outbox relies on.
- [`Safe_and_Scalable_API_calls/11_idempotency.md`](../../fundamentals/fastapi/Safe_and_Scalable_API_calls/11_idempotency.md) — the consumer-side idempotency that makes at-least-once safe.
