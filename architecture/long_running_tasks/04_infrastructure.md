# Part 4: Infrastructure & Technology Choices

Concrete tools and services that implement the patterns from Parts 1–3. For each, we cover: what it is, which patterns it supports, how to use it, and when to pick it.

---

## 1. Redis

The Swiss Army knife. Redis can serve as the queue, the result store, the heartbeat store, and the pub/sub notification layer — all at once.

### As a Task Queue (Redis Lists + BLMOVE)

```python
import redis.asyncio as redis

# Producer (orchestrator): enqueue task
await r.lpush("tasks", json.dumps({"job_id": "abc", "payload": {...}}))

# Consumer (worker): blocking pop with reliability
# BLMOVE atomically pops from "tasks" (RIGHT) and pushes to "processing" (LEFT).
# BRPOPLPUSH is the older equivalent, deprecated since Redis 6.2.0.
raw = await r.blmove("tasks", "processing", timeout=30, src="RIGHT", dest="LEFT")
task = json.loads(raw)

# After processing, remove from "processing"
await r.lrem("processing", 1, raw)
```

The `processing` list acts as an in-flight buffer. If the worker crashes, a sweeper can move items from `processing` back to `tasks`.

### As a Result Store

```python
# Worker writes result
await r.set(f"result:{job_id}", json.dumps(result), ex=3600)  # 1h TTL

# Client reads result
raw = await r.get(f"result:{job_id}")
if raw:
    result = json.loads(raw)
```

### As a Heartbeat Store

```python
# Worker: update heartbeat with TTL
await r.set(f"heartbeat:{job_id}", datetime.utcnow().isoformat(), ex=90)

# Monitor: check heartbeat
alive = await r.exists(f"heartbeat:{job_id}")
# If key expired (TTL elapsed) → worker is dead
```

Using Redis TTL as an implicit heartbeat is elegant: the key auto-deletes if the worker stops refreshing it. No sweeper needed.

### As Pub/Sub Notification

```python
# Orchestrator: publish result when worker completes
await r.publish(f"job:{job_id}", json.dumps({"status": "done", "result": result}))

# Client: subscribe and wait
pubsub = r.pubsub()
await pubsub.subscribe(f"job:{job_id}")
async for msg in pubsub.listen():
    if msg["type"] == "message":
        return json.loads(msg["data"])
```

### Redis Streams (Modern Alternative to Lists)

Redis Streams are purpose-built for task queues with consumer groups, acknowledgements, and replay.

```python
# Producer
await r.xadd("task_stream", {"job_id": "abc", "payload": json.dumps(data)})

# Consumer (in a consumer group)
results = await r.xreadgroup(
    groupname="workers",
    consumername="worker-1",
    streams={"task_stream": ">"},  # ">" = only new messages
    count=1,
    block=5000,  # Block for 5 seconds
)

# Acknowledge after processing
await r.xack("task_stream", "workers", message_id)
```

**Advantages over Lists:**
- Consumer groups (multiple workers, each gets different messages)
- Acknowledgement (unacked messages can be reclaimed by other workers)
- Message replay (re-read old messages)
- Built-in pending message tracking

### When to Use Redis

| Use Case | Redis Feature | Alternative |
|----------|---------------|-------------|
| Simple task queue | Lists + BLMOVE | SQS, RabbitMQ |
| Task queue with consumer groups | Streams | Kafka, RabbitMQ |
| Result cache | SET with TTL | S3, DynamoDB |
| Heartbeat tracking | SET with TTL | DynamoDB, PostgreSQL |
| Real-time notification | Pub/Sub | WebSocket, SSE |
| Job status tracking | Hashes | PostgreSQL, DynamoDB |

**Pick Redis when:** You need multiple of the above in one system, latency matters (sub-ms), and you're comfortable with at-most-once delivery (pub/sub) or at-least-once (Streams).

**Avoid Redis when:** You need durable message persistence (Redis persistence is async), guaranteed exactly-once delivery, or you're processing millions of messages per second (Kafka territory).

---

## 2. RabbitMQ (AMQP)

A traditional message broker with rich routing, acknowledgements, and delivery guarantees.

### As a Task Queue

```python
import aio_pika

connection = await aio_pika.connect_robust("amqp://guest:guest@localhost/")
channel = await connection.channel()
await channel.set_qos(prefetch_count=1)  # One task at a time

queue = await channel.declare_queue("tasks", durable=True)

# Producer
await channel.default_exchange.publish(
    aio_pika.Message(body=json.dumps(task).encode(), delivery_mode=2),  # persistent
    routing_key="tasks",
)

# Consumer
async for message in queue:
    async with message.process():  # Auto-ack on success, nack on exception
        task = json.loads(message.body)
        await process(task)
```

### Key Features

| Feature | Detail |
|---------|--------|
| **Acknowledgements** | Manual ack/nack — message stays in queue until explicitly acknowledged |
| **Dead Letter Exchange** | Failed messages are routed to a DLX after N retries |
| **Priority Queues** | Messages can have priority levels |
| **TTL** | Per-message and per-queue TTL |
| **Routing** | Topic exchanges, fanout exchanges, header-based routing |

### When to Use RabbitMQ

**Pick RabbitMQ when:** You need complex routing (one message to multiple queues based on rules), message priority, or you're in an on-premise environment without cloud-managed queues.

**Avoid RabbitMQ when:** You need event replay/rewind (RabbitMQ deletes messages after consumption), you need massive throughput (millions/s — Kafka territory), or you want zero operational overhead (use SQS).

---

## 3. Amazon SQS

Fully managed queue service. Zero operational overhead — no servers, no patching, no capacity planning.

### Standard Queue (At-Least-Once, Best-Effort Ordering)

```python
import boto3

sqs = boto3.client("sqs")

# Producer
sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(task))

# Consumer
response = sqs.receive_message(QueueUrl=QUEUE_URL, MaxNumberOfMessages=1, WaitTimeSeconds=20)
for msg in response.get("Messages", []):
    process(json.loads(msg["Body"]))
    sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])
```

### FIFO Queue (Exactly-Once, Ordered)

```python
sqs.send_message(
    QueueUrl=FIFO_QUEUE_URL,
    MessageBody=json.dumps(task),
    MessageGroupId=str(hash(job_id) % NUM_GROUPS),  # Concurrency control
    MessageDeduplicationId=job_id,  # Prevent duplicate enqueue
)
```

FIFO queues guarantee:
- **Exactly-once delivery** within the 5-minute deduplication interval (processing still must be idempotent to survive consumer crashes after receive but before delete)
- **Ordered delivery** within a message group
- **One in-flight message per group** — natural concurrency limiter

### Lambda Event Source Mapping (ESM)

```hcl
# Terraform
resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.tasks.arn
  function_name    = aws_lambda_function.worker.arn
  batch_size       = 1

  scaling_config {
    maximum_concurrency = 10  # Max 10 Lambda invocations
  }
}
```

The ESM automatically:
- Polls the queue
- Invokes the Lambda with the message
- Deletes the message on successful Lambda return
- Sends to DLQ on repeated failure

### When to Use SQS

**Pick SQS when:** You're on AWS, want zero ops, need reliable delivery, and your throughput is moderate (default FIFO: 300 msg/s per API action, or 3,000/s with batching; high-throughput FIFO mode scales much higher; Standard queues are effectively unlimited).

**Avoid SQS when:** You need message replay, complex routing, or you're not on AWS.

---

## 4. Apache Kafka

Distributed event log. Messages are persisted to disk and can be replayed. Built for very high throughput.

### As a Task Queue

```python
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer

# Producer
producer = AIOKafkaProducer(bootstrap_servers="kafka:9092")
await producer.send_and_wait("tasks", json.dumps(task).encode())

# Consumer (in a consumer group)
consumer = AIOKafkaConsumer(
    "tasks",
    bootstrap_servers="kafka:9092",
    group_id="workers",
    enable_auto_commit=False,
)
async for msg in consumer:
    task = json.loads(msg.value)
    await process(task)
    await consumer.commit()
```

### Key Differentiators

| Feature | Kafka | SQS / RabbitMQ |
|---------|-------|----------------|
| **Message retention** | Days–weeks (configurable) | Deleted after consumption |
| **Replay** | Seek to any offset | Not possible |
| **Throughput** | Millions/second | Thousands/second |
| **Ordering** | Per-partition (guaranteed) | Per-group (FIFO) or best-effort |
| **Consumer groups** | Native, with rebalancing | Manual or limited |

### When to Use Kafka

**Pick Kafka when:** You need event replay (audit, debugging), very high throughput, or you want a durable event log that multiple services consume independently.

**Avoid Kafka when:** You want simplicity (Kafka is operationally complex), your volume is low (< 1K messages/s — overkill), or you need request-reply semantics (Kafka is one-directional).

---

## 5. AWS Step Functions

Managed orchestrator with visual workflow designer. Natively supports the Task Token pattern, retries, error handling, and parallel execution.

### Task Token Pattern (Native)

```json
{
  "StartAt": "InvokeWorker",
  "States": {
    "InvokeWorker": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke.waitForTaskToken",
      "Parameters": {
        "FunctionName": "worker-lambda",
        "Payload": {
          "job_id.$": "$.job_id",
          "task_token.$": "$$.Task.Token"
        }
      },
      "HeartbeatSeconds": 900,
      "TimeoutSeconds": 3600,
      "Catch": [{
        "ErrorEquals": ["States.Timeout", "States.TaskFailed"],
        "Next": "HandleFailure"
      }],
      "Next": "StoreResult"
    },
    "StoreResult": { "Type": "Pass", "End": true },
    "HandleFailure": { "Type": "Fail" }
  }
}
```

### Distributed Map (Batch Processing)

```json
{
  "Type": "Map",
  "ItemProcessor": {
    "ProcessorConfig": {
      "Mode": "DISTRIBUTED",
      "ExecutionType": "STANDARD"
    },
    "StartAt": "ProcessItem",
    "States": {
      "ProcessItem": {
        "Type": "Task",
        "Resource": "arn:aws:lambda:...",
        "End": true
      }
    }
  },
  "MaxConcurrency": 10
}
```

### When to Use Step Functions

**Pick Step Functions when:** You're on AWS and need managed orchestration with visual debugging, built-in retries, and task token support. Particularly good for long-running workflows (hours) because you pay per state transition, not per second.

**Avoid Step Functions when:** You're not on AWS, your workflows are simple (just a queue + worker), or you need sub-second latency (Step Functions adds ~100ms per state transition).

---

## 6. Celery

Python-native distributed task queue. Workers are Python processes.

### Setup

```python
from celery import Celery

app = Celery("tasks", broker="redis://localhost:6379/0", backend="redis://localhost:6379/1")

@app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_job(self, job_id: str, payload: dict):
    try:
        result = do_inference(payload)
        store_result(job_id, result)
    except Exception as exc:
        self.retry(exc=exc)
```

### Client (Submit and Wait)

```python
# Fire and forget
result = process_job.delay(job_id, payload)

# Wait for result (blocking)
result = process_job.delay(job_id, payload)
output = result.get(timeout=600)  # Blocks for up to 10 minutes

# Check status (polling)
result = process_job.delay(job_id, payload)
while not result.ready():
    time.sleep(2)
output = result.get()
```

### When to Use Celery

**Pick Celery when:** Your stack is Python, you want rich task features (retries, rate limits, priority, chords, chains), and you're OK running worker processes.

**Avoid Celery when:** You need language-agnostic workers, you want serverless execution, or you need very high throughput (Celery's overhead per task is higher than raw Redis/SQS).

---

## 7. Temporal

Open-source durable execution platform. Workflows survive process crashes, restarts, and deployments. Stronger guarantees than Step Functions.

### Workflow Definition

```python
from temporalio import workflow, activity
from datetime import timedelta

@activity.defn
async def process_inference(job_id: str, payload: dict) -> dict:
    return await do_inference(payload)

@workflow.defn
class InferenceWorkflow:
    @workflow.run
    async def run(self, job_id: str, payload: dict) -> dict:
        result = await workflow.execute_activity(
            process_inference,
            args=[job_id, payload],
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(seconds=60),
        )
        return result
```

### When to Use Temporal

**Pick Temporal when:** You need durable execution (workflow survives any infrastructure failure), complex multi-step workflows, or you want the strongest possible delivery guarantees.

**Avoid Temporal when:** Your workflows are simple (queue + worker), you don't want to run the Temporal server, or you're fully committed to a cloud provider's native tools.

---

## 8. PostgreSQL (LISTEN / NOTIFY + Advisory Locks)

Your database can be the queue. Not the most scalable option, but eliminates infrastructure when you already have PostgreSQL.

### As a Task Queue (SELECT FOR UPDATE SKIP LOCKED)

```sql
-- Create jobs table
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status TEXT DEFAULT 'pending',
    payload JSONB,
    result JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Worker: atomically claim one pending job
UPDATE jobs
SET status = 'running', updated_at = NOW()
WHERE id = (
    SELECT id FROM jobs
    WHERE status = 'pending'
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
```

### As Real-Time Notification (LISTEN / NOTIFY)

```python
import asyncpg

# Worker/Orchestrator: notify when job completes
await conn.execute("NOTIFY job_updates, $1", json.dumps({"job_id": job_id, "status": "done"}))

# Client: listen for notifications
await conn.add_listener("job_updates", lambda conn, pid, channel, payload: handle(payload))
```

### When to Use PostgreSQL as Queue

**Pick PostgreSQL when:** Your volume is low (< 100 jobs/second), you already have PostgreSQL, and you want to avoid adding infrastructure.

**Avoid PostgreSQL when:** Your volume is high (database becomes a bottleneck), you need long message retention, or you need consumer groups.

---

## Decision Matrix

| Need | Best Choice | Runner-Up |
|------|-------------|-----------|
| **Simple async task queue** | SQS (AWS) or Redis Streams | RabbitMQ |
| **Long-running orchestration** | Step Functions (AWS) or Temporal | Celery (simple cases) |
| **Real-time result notification** | Redis Pub/Sub or WebSocket | SSE |
| **Result storage (small)** | Redis (TTL) or PostgreSQL | DynamoDB |
| **Result storage (large)** | S3 / Blob Storage | — |
| **Heartbeat tracking** | Redis (TTL keys) | DynamoDB |
| **Complex routing** | RabbitMQ | Kafka |
| **Event replay / audit** | Kafka | Redis Streams |
| **Zero ops** | SQS + Lambda + Step Functions | — |
| **Already have PostgreSQL, low volume** | PostgreSQL SKIP LOCKED + LISTEN/NOTIFY | — |
| **Python-native workers** | Celery (Redis broker) | — |

---

## Reference Architecture: Putting It All Together

A production system for batched LLM inference using the patterns from this guide:

```
                                    ┌─────────────────────────┐
                                    │      Redis              │
                                    │  - Task queue (Streams) │
                                    │  - Result store (TTL)   │
                                    │  - Heartbeat (TTL keys) │
                                    │  - Pub/Sub notification │
                                    └───────┬────┬────┬───────┘
                                            │    │    │
┌──────────┐    ┌────────────────┐          │    │    │      ┌──────────────┐
│  Client   │──►│  Orchestrator  │──enqueue─┘    │    │      │   Worker(s)  │
│  (any of: │   │  (FastAPI)     │◄──heartbeat───┘    │      │  - Pull from │
│  WS/SSE/  │   │                │◄──result───────────┘      │    Stream    │
│  poll/    │   │  - POST /jobs  │                           │  - Batch     │
│  webhook) │   │  - GET /jobs/  │                           │    collect   │
│           │◄──│  - WS /ws/     │           ┌──subscribe──► │  - Process   │
└──────────┘    │  - SSE /stream │           │               │  - Heartbeat │
                └────────────────┘           │               │  - Callback  │
                         │                   │               └──────────────┘
                         │                   │
                         └── notify clients  │
                             via Redis pub/sub
```

**Flow:**
1. Client POSTs to `/jobs` → gets `202 {job_id}`
2. Orchestrator enqueues to Redis Stream
3. Worker pulls from Stream (consumer group), batches items, runs inference
4. Worker sends heartbeat (Redis TTL key) every 30s
5. Worker writes result to Redis, publishes to pub/sub channel
6. Orchestrator receives pub/sub notification, pushes to connected WebSocket/SSE clients
7. Polling clients hit `GET /jobs/{id}` → orchestrator reads from Redis
8. Webhook clients receive a POST to their callback URL
