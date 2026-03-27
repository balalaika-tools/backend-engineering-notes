# Part 2: Worker Patterns

How the worker receives tasks, executes them, reports health, handles failures, and delivers results. The worker is the execution engine — everything else exists to support it.

---

## 1. Task Pickup Strategies

### Pull-Based (Worker Polls Queue)

The worker pulls tasks from a queue (SQS, RabbitMQ, Redis list). The worker controls its own pace.

```
Queue: [task_3] [task_2] [task_1]
                                    ◄── worker.poll()
                          [task_1]  ──► worker processes
Queue: [task_3] [task_2]
                                    ◄── worker.poll()
                [task_2]            ──► worker processes
Queue: [task_3]
```

**Advantages:**
- Natural backpressure — worker only pulls when ready
- Worker can batch-pull multiple tasks at once
- Simple scaling — add more workers, they all pull from the same queue

**Disadvantages:**
- Polling interval adds latency (empty polls waste resources)
- Need visibility timeout management (what if worker crashes mid-processing?)

### Push-Based (Orchestrator Invokes Worker)

The orchestrator calls the worker directly (Lambda invoke, HTTP call, gRPC).

```
Orchestrator ── invoke(task) ──► Worker
                                  │── process
Orchestrator ◄── response ────────┘
```

**Advantages:**
- Zero dispatch latency
- Orchestrator has direct control over which worker gets which task

**Disadvantages:**
- Orchestrator must manage concurrency (don't overload workers)
- Tighter coupling — orchestrator needs to know worker addresses/endpoints

### Event-Triggered

An event source (SQS + Lambda ESM, Kafka consumer group) automatically triggers the worker when a new task arrives.

```
Queue: new message arrives
  │
  └── Event Source Mapping triggers Lambda / container
      │
      └── Worker processes message
```

**Advantages:**
- Zero-config autoscaling (Lambda) or managed consumer groups (Kafka)
- No polling overhead

**Disadvantages:**
- Less control over concurrency (need explicit limits like Lambda reserved concurrency, ESM MaximumConcurrency)
- Cold starts (Lambda)

---

## 2. Health Reporting

The worker must communicate its liveness to the orchestrator. How it does so depends on the orchestration pattern.

### Heartbeat to Orchestrator (Active)

Worker sends periodic signals. See [Orchestration Patterns: Heartbeat Monitoring](01_orchestration_patterns.md#pattern-2-heartbeat-monitoring).

```python
import asyncio
import httpx

async def heartbeat_sender(job_id: str, url: str, interval: float = 30.0):
    async with httpx.AsyncClient() as client:
        while True:
            await client.post(f"{url}/jobs/{job_id}/heartbeat")
            await asyncio.sleep(interval)
```

### Status Writes to Shared Store (Passive)

Worker periodically writes its status to a shared store (Redis, DynamoDB, PostgreSQL). The orchestrator reads it when needed.

```python
async def update_progress(redis: Redis, job_id: str, progress: int, step: str):
    await redis.hset(f"job:{job_id}", mapping={
        "progress": progress,
        "step": step,
        "updated_at": datetime.utcnow().isoformat(),
    })
    # TTL acts as implicit heartbeat — if the key expires, the worker is dead
    await redis.expire(f"job:{job_id}", 120)  # 2x heartbeat interval
```

### Health Endpoint (Infrastructure-Level)

The worker exposes a `/health` endpoint. The orchestrator (or a load balancer / container orchestrator) pings it.

```python
# Worker exposes this
@app.get("/health")
async def health():
    return {"status": "healthy", "active_tasks": len(running_tasks)}
```

This is the pattern used by Kubernetes liveness/readiness probes, ECS health checks, and Bedrock AgentCore's `HealthyBusy` / `Healthy` ping states.

---

## 3. Result Storage Patterns

Where does the worker put the result when it's done?

### Direct Callback (Push Result to Orchestrator)

Worker POSTs the result directly to the orchestrator or a result endpoint.

```python
async def complete_task(orchestrator_url: str, job_id: str, result: dict):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{orchestrator_url}/jobs/{job_id}/complete",
            json={"status": "succeeded", "result": result},
        )
```

**Best for:** Small results (< 1 MB), when the orchestrator needs to react immediately.

### Write to Shared Store (Decoupled)

Worker writes the result to a store. The orchestrator (or client) reads it when ready.

```python
# Worker writes
await redis.set(f"result:{job_id}", json.dumps(result), ex=3600)

# Or to S3 for large results
s3.put_object(Bucket="results", Key=f"{job_id}.json", Body=json.dumps(result))
```

**Best for:** Large results, when multiple consumers need the result, when you want result persistence.

### Write to Database Row

Worker updates the job record directly.

```python
await db.execute(
    "UPDATE jobs SET status = 'succeeded', result = $1, completed_at = NOW() WHERE id = $2",
    json.dumps(result), job_id,
)
```

**Best for:** Simple architectures where the job table is the single source of truth.

### Comparison

| Strategy | Result Size | Latency | Coupling | Persistence |
|----------|-------------|---------|----------|-------------|
| Direct callback | Small (< 1 MB) | Lowest | High | No (transient) |
| Redis | Medium (< 50 MB) | Low | Low | TTL-based |
| S3 / Blob Storage | Any (GBs) | Medium | Low | Durable |
| Database row | Small–Medium | Low | Medium | Durable |

---

## 4. Graceful vs Ungraceful Failure

### Graceful Failure

The worker catches the error, reports it, and exits cleanly.

```python
async def run_task(job_id: str, task_token: str):
    try:
        result = await do_inference(job_id)
        await send_task_success(task_token, result)
    except Exception as e:
        # Graceful: explicitly report failure
        await send_task_failure(task_token, error=str(e), cause=traceback.format_exc())
        logger.error(f"Task {job_id} failed gracefully", exc_info=True)
```

**What the orchestrator sees:** An explicit failure signal with error details. Can make informed retry/escalation decisions.

### Ungraceful Failure

The worker crashes (OOM, SIGKILL, hardware failure, network partition). No callback is sent.

```
Worker: *crashes*
  │
  ├── No callback sent
  ├── No heartbeat sent
  │
  └── Orchestrator detects via:
      ├── Heartbeat timeout (Pattern 2)
      ├── HeartbeatSeconds expiry (Pattern 3 — Task Token)
      ├── Stale-job sweeper (Pattern 1 — Fire-and-Forget)
      └── Visibility timeout expiry → message reappears in queue
```

**What the orchestrator sees:** Silence. Must infer failure from absence of signal.

### Designing for Both

```python
async def run_task(job_id: str, task_token: str):
    heartbeat_task = asyncio.create_task(heartbeat_loop(job_id, task_token))
    try:
        result = await do_inference(job_id)
        await send_task_success(task_token, result)        # graceful success
    except Exception as e:
        await send_task_failure(task_token, error=str(e))  # graceful failure
    finally:
        heartbeat_task.cancel()
    # If the process is killed before reaching here:
    # - Heartbeat stops → orchestrator detects ungraceful failure
    # - Message visibility timeout expires → task re-enqueued (if queue-based)
```

---

## 5. Batch Collection

When the worker processes multiple items as a batch (e.g., batched LLM inference for throughput), it needs to collect individual requests into groups.

### Time-Window Batching

Collect items for a fixed time window, then process whatever has accumulated.

```python
async def batch_collector(queue: asyncio.Queue, window_seconds: float = 2.0, max_batch: int = 32):
    while True:
        batch = []
        deadline = asyncio.get_running_loop().time() + window_seconds

        # Collect until window closes or batch is full
        while asyncio.get_running_loop().time() < deadline and len(batch) < max_batch:
            remaining = deadline - asyncio.get_running_loop().time()
            try:
                item = await asyncio.wait_for(queue.get(), timeout=max(0, remaining))
                batch.append(item)
            except asyncio.TimeoutError:
                break  # Window closed

        if batch:
            await process_batch(batch)
```

### Size-Triggered Batching

Process when the batch reaches a target size. Use a timeout as a fallback for low-traffic periods.

```python
async def batch_collector(queue: asyncio.Queue, batch_size: int = 32, max_wait: float = 5.0):
    while True:
        batch = [await queue.get()]  # Block until at least one item
        deadline = asyncio.get_running_loop().time() + max_wait

        while len(batch) < batch_size:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=remaining)
                batch.append(item)
            except asyncio.TimeoutError:
                break

        await process_batch(batch)
```

### Adaptive Batching

Adjust batch size based on current load. Under high load, batch more aggressively for throughput. Under low load, process quickly for latency.

```python
async def adaptive_batch_collector(queue: asyncio.Queue):
    min_batch, max_batch = 1, 64
    current_target = 8

    while True:
        batch = [await queue.get()]
        pending = queue.qsize()

        # Adapt: if queue is deep, batch more; if shallow, batch less
        if pending > current_target * 2:
            current_target = min(current_target * 2, max_batch)
        elif pending < current_target // 2:
            current_target = max(current_target // 2, min_batch)

        # Collect up to target
        while len(batch) < current_target and not queue.empty():
            batch.append(queue.get_nowait())

        await process_batch(batch)
```

---

## 6. Idempotency

Workers should be idempotent — processing the same task twice must produce the same result (or at least not cause harm). This matters because:

- Queue messages can be delivered more than once (SQS at-least-once)
- The orchestrator might retry on timeout (worker was actually alive, just slow)
- Network partitions can cause duplicate dispatches

### Idempotency Key

Every task has a unique key. Before processing, the worker checks if it already processed this key.

```python
async def process_if_new(redis: Redis, task_id: str, task_data: dict):
    # SET NX = set only if not exists. Returns True if set, False if already exists.
    is_new = await redis.set(f"processed:{task_id}", "1", nx=True, ex=86400)

    if not is_new:
        logger.info(f"Task {task_id} already processed, skipping")
        return

    await do_actual_processing(task_data)
```

### Idempotent Result Storage

If the result is already stored, a duplicate write is a no-op (or an upsert).

```sql
-- PostgreSQL: INSERT ... ON CONFLICT DO NOTHING
INSERT INTO results (job_id, result, created_at)
VALUES ($1, $2, NOW())
ON CONFLICT (job_id) DO NOTHING;
```

---

## 7. Concurrency Control on the Worker

How to limit how many tasks a single worker processes simultaneously.

### Semaphore (In-Process)

```python
import asyncio

_semaphore = asyncio.Semaphore(4)  # Max 4 concurrent tasks

async def handle_task(task):
    async with _semaphore:
        await process(task)
```

### Queue-Level (External)

- **SQS**: `MaximumConcurrency` on the Event Source Mapping
- **RabbitMQ**: `prefetch_count` on the consumer channel
- **Celery**: `--concurrency` flag on the worker
- **Kubernetes**: Horizontal Pod Autoscaler limits

### Resource-Based

Monitor GPU memory, CPU, or RAM and stop accepting tasks when saturated.

```python
async def can_accept_task() -> bool:
    gpu_util = get_gpu_utilization()  # nvidia-smi or pynvml
    return gpu_util < 0.85  # 85% threshold
```
