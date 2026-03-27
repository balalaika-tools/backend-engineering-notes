# Redis Pub/Sub & Streams

> **Two messaging models, one database.** Redis offers two distinct ways to pass messages between producers and consumers. Pub/Sub is fire-and-forget: fast, ephemeral, no history. Streams are durable: messages persist, can be replayed, and support consumer groups. Knowing when to use each is the key decision.

Mental model:

> **Pub/Sub** = a loudspeaker in a room. If you are not in the room, you miss the message.
> **Streams** = a bulletin board. Messages stay posted. You can read them anytime, and mark which ones you have processed.

---

## Overview

| | Pub/Sub | Streams |
|---|---------|---------|
| Model | Fire-and-forget broadcast | Durable append-only log |
| Persistence | None — messages are lost if no subscriber is listening | Yes — messages persist until explicitly deleted or trimmed |
| Consumer groups | No | Yes |
| Message replay | Impossible | Read from any point in history |
| Delivery | At-most-once | At-least-once (with consumer groups) |
| Best for | Real-time notifications, cache invalidation | Event sourcing, job queues, audit logs |

---

## Part 1: Classic Pub/Sub

### How Pub/Sub Works

1. **Subscribers** listen on one or more channels.
2. **Publishers** send messages to a channel.
3. Redis delivers the message to **all current subscribers** of that channel.
4. If no one is subscribed, the message is **silently dropped**.

```
Publisher                Redis                 Subscribers
─────────              ─────────              ───────────
PUBLISH "chat" "hi" ──> channel: "chat" ──> Subscriber A (listening)
                                         ──> Subscriber B (listening)
                                              Subscriber C (offline) <-- missed
```

### Core Commands

| Command | Description | Example |
|---------|-------------|---------|
| `SUBSCRIBE channel` | Listen to a channel | `SUBSCRIBE notifications` |
| `UNSUBSCRIBE channel` | Stop listening | `UNSUBSCRIBE notifications` |
| `PUBLISH channel message` | Send a message | `PUBLISH notifications "new_order"` |
| `PSUBSCRIBE pattern` | Listen to channels matching a pattern | `PSUBSCRIBE events:*` |
| `PUNSUBSCRIBE pattern` | Stop pattern listening | `PUNSUBSCRIBE events:*` |

### Python Sync Example

```python
import redis
import json
import threading

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

# --- Publisher ---
def publish_event(channel: str, data: dict):
    message = json.dumps(data)
    num_subscribers = r.publish(channel, message)
    print(f"Published to {num_subscribers} subscriber(s)")

publish_event("notifications", {"type": "order_created", "order_id": 42})

# --- Subscriber ---
def subscribe_loop(channels: list[str]):
    pubsub = r.pubsub()
    pubsub.subscribe(*channels)

    print(f"Subscribed to {channels}")
    for message in pubsub.listen():
        if message["type"] == "message":
            data = json.loads(message["data"])
            print(f"Received on {message['channel']}: {data}")

# Run subscriber in a thread (it blocks)
thread = threading.Thread(target=subscribe_loop, args=(["notifications"],), daemon=True)
thread.start()
```

### Python Async Example

```python
import redis.asyncio as aioredis
import asyncio
import json

async def subscriber(channel_name: str):
    r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(channel_name)

    print(f"Listening on {channel_name}...")
    async for message in pubsub.listen():
        if message["type"] == "message":
            data = json.loads(message["data"])
            print(f"Received: {data}")
            # process the message...

    await r.aclose()

async def publisher():
    r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)

    for i in range(5):
        await r.publish("events", json.dumps({"event_id": i, "type": "test"}))
        await asyncio.sleep(1)

    await r.aclose()

async def main():
    # Run subscriber and publisher concurrently
    async with asyncio.TaskGroup() as tg:
        tg.create_task(subscriber("events"))
        tg.create_task(publisher())

asyncio.run(main())
```

### Pattern Subscriptions

Subscribe to multiple channels using glob patterns:

```python
pubsub = r.pubsub()

# Subscribe to all channels starting with "events:"
pubsub.psubscribe("events:*")

# Matches: events:orders, events:payments, events:users
# Does not match: events, notifications:orders

for message in pubsub.listen():
    if message["type"] == "pmessage":
        channel = message["channel"]    # "events:orders"
        pattern = message["pattern"]    # "events:*"
        data = message["data"]          # the message
        print(f"[{channel}] {data}")
```

### When Pub/Sub Works Well

- **Real-time notifications**: Push updates to connected WebSocket clients.
- **Cache invalidation**: Tell all app instances to invalidate a cached key.
- **WebSocket fan-out**: Broadcast messages to all connected users in a chat room.
- **Inter-service coordination**: Signal other services that something happened (and it is OK if they miss it).
- **Feature flag updates**: Notify running instances of configuration changes.

### When Pub/Sub Fails

- **Subscriber goes offline**: Messages during the downtime are permanently lost.
- **Slow subscribers**: Redis buffers messages in memory. A slow subscriber can cause Redis memory to grow unboundedly (configurable via `client-output-buffer-limit`).
- **Exactly-once processing**: Not possible. No acknowledgments, no retries.
- **Message history**: Cannot replay past messages. No way to ask "what did I miss?"
- **Consumer groups**: No built-in way to distribute messages across multiple workers (every subscriber gets every message).

> **If you need any of the above, use Streams instead.**

---

## Part 2: Streams

### How Streams Work

A stream is an **append-only log**. Each entry has:
- A unique **ID** (auto-generated, based on timestamp: `1679012345678-0`)
- A set of **field-value pairs** (the message data)

Messages persist until explicitly deleted or trimmed. Multiple consumers can read the same stream independently, at their own pace.

```
Stream: "events:orders"
──────────────────────────────────────────────────────────
ID                  Data
1679012345678-0     {"type": "created", "order_id": "1"}
1679012345679-0     {"type": "paid",    "order_id": "1"}
1679012345680-0     {"type": "created", "order_id": "2"}
                    entries persist, can be read anytime
```

### Basic Stream Operations (Async Python)

```python
import redis.asyncio as aioredis
import asyncio

async def stream_demo():
    r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)

    # Add entries to a stream
    entry_id = await r.xadd("events:orders", {
        "type": "order_created",
        "order_id": "42",
        "total": "99.99"
    })
    print(f"Added entry: {entry_id}")

    # Add with a max length (auto-trim old entries)
    await r.xadd("events:orders", {
        "type": "order_paid",
        "order_id": "42"
    }, maxlen=10000)  # keep at most 10,000 entries

    # Approximate trimming (more efficient, slight overcount OK)
    await r.xadd("events:orders", {
        "type": "order_shipped",
        "order_id": "42"
    }, maxlen=10000, approximate=True)  # ~10,000 entries

    # Read all entries
    entries = await r.xrange("events:orders", "-", "+")
    for entry_id, data in entries:
        print(f"  {entry_id}: {data}")

    # Read entries after a specific ID
    entries = await r.xrange("events:orders", "1679012345678-0", "+")

    # Read last 5 entries
    entries = await r.xrevrange("events:orders", "+", "-", count=5)

    # Stream length
    length = await r.xlen("events:orders")
    print(f"Stream has {length} entries")

    await r.aclose()

asyncio.run(stream_demo())
```

### Simple Consumer (Blocking Read)

A simple consumer reads from a stream position and moves forward. Good for single-consumer scenarios or when you manage offsets yourself.

```python
async def stream_listener(r, stream_name: str):
    """Listen for new entries on a stream, starting from now."""
    last_id = "$"  # "$" means "only new entries from this point"

    while True:
        # Block for up to 5 seconds waiting for new entries
        result = await r.xread(
            streams={stream_name: last_id},
            count=10,
            block=5000  # milliseconds
        )

        if not result:
            continue  # timeout, no new entries

        for stream, entries in result:
            for entry_id, data in entries:
                print(f"New entry {entry_id}: {data}")
                last_id = entry_id  # track position
```

---

### Consumer Groups

Consumer groups are the most powerful feature of Redis Streams. They allow **multiple workers to cooperatively process a stream**, with each message delivered to exactly one worker in the group.

```
Stream: "events:orders"
          |
          +---> Consumer Group "order-processors"
          |       +-- worker-1 (gets some messages)
          |       +-- worker-2 (gets other messages)
          |       +-- worker-3 (gets the rest)
          |
          +---> Consumer Group "analytics"
                  +-- analytics-1 (gets some messages)
                  +-- analytics-2 (gets the rest)
```

Key concepts:

- **Each message is delivered to exactly one consumer within a group** (load distribution).
- **Different groups are independent** — each group sees every message.
- **Messages must be acknowledged** (`XACK`) — unacknowledged messages can be reclaimed.
- **Pending entries list (PEL)** tracks delivered-but-not-acknowledged messages.

### Consumer Group: Complete Example

```python
import redis.asyncio as aioredis
import asyncio

STREAM = "events:orders"
GROUP = "order-processors"

async def setup_consumer_group(r):
    """Create the stream and consumer group if they don't exist."""
    try:
        await r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        print(f"Created consumer group '{GROUP}'")
    except aioredis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            pass  # group already exists
        else:
            raise

async def consumer(r, consumer_name: str):
    """
    Consume messages from the stream as part of a consumer group.
    Each message is delivered to exactly one consumer in the group.
    """
    print(f"[{consumer_name}] Starting...")

    while True:
        try:
            # Read messages not yet delivered to any consumer in this group
            result = await r.xreadgroup(
                groupname=GROUP,
                consumername=consumer_name,
                streams={STREAM: ">"},  # ">" = only new, undelivered messages
                count=5,
                block=5000
            )

            if not result:
                continue

            for stream, messages in result:
                for msg_id, data in messages:
                    print(f"[{consumer_name}] Processing {msg_id}: {data}")

                    # Simulate work
                    await asyncio.sleep(0.1)

                    # Acknowledge the message (removes it from the pending list)
                    await r.xack(STREAM, GROUP, msg_id)
                    print(f"[{consumer_name}] ACKed {msg_id}")

        except asyncio.CancelledError:
            print(f"[{consumer_name}] Shutting down...")
            break

async def producer(r, count: int = 20):
    """Produce test messages."""
    for i in range(count):
        entry_id = await r.xadd(STREAM, {
            "type": "order_created",
            "order_id": str(i),
            "total": f"{(i + 1) * 10:.2f}"
        })
        print(f"[producer] Added {entry_id}")
        await asyncio.sleep(0.2)

async def main():
    r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)

    await setup_consumer_group(r)

    async with asyncio.TaskGroup() as tg:
        # Start 3 consumers
        tg.create_task(consumer(r, "worker-1"))
        tg.create_task(consumer(r, "worker-2"))
        tg.create_task(consumer(r, "worker-3"))
        # Start producer
        tg.create_task(producer(r, count=20))

    await r.aclose()

asyncio.run(main())
```

### Pending Entries List (PEL) & Recovery

When a consumer reads a message via `XREADGROUP`, that message enters the **Pending Entries List (PEL)**. It stays there until the consumer calls `XACK`. If the consumer crashes, the message remains pending.

```python
async def check_pending(r):
    """Inspect the pending entries list."""
    # Summary: how many pending messages per consumer
    pending_info = await r.xpending(STREAM, GROUP)
    print(f"Total pending: {pending_info['pending']}")
    for consumer_info in pending_info.get("consumers", []):
        print(f"  {consumer_info['name']}: {consumer_info['pending']} pending")

    # Detailed: list specific pending messages
    pending_messages = await r.xpending_range(
        name=STREAM,
        groupname=GROUP,
        min="-",
        max="+",
        count=20
    )
    for msg in pending_messages:
        print(f"  ID: {msg['message_id']}, consumer: {msg['consumer']}, "
              f"idle: {msg['time_since_delivered']}ms, deliveries: {msg['times_delivered']}")
```

#### Reclaiming Failed Messages

```python
async def reclaim_stale_messages(r, consumer_name: str, min_idle_ms: int = 60000):
    """
    Claim messages that have been pending for too long (consumer probably crashed).
    min_idle_ms: only claim messages idle for at least this many milliseconds.
    """
    # XAUTOCLAIM: automatically claim stale messages (Redis 6.2+)
    # This is the modern, preferred approach
    result = await r.xautoclaim(
        name=STREAM,
        groupname=GROUP,
        consumername=consumer_name,
        min_idle_time=min_idle_ms,
        start_id="0-0",
        count=10
    )

    next_id, claimed, deleted = result
    for msg_id, data in claimed:
        print(f"[{consumer_name}] Reclaimed stale message {msg_id}: {data}")
        # Process and ACK
        await r.xack(STREAM, GROUP, msg_id)

    return len(claimed)

# XCLAIM: manually claim specific message IDs (older approach)
claimed = await r.xclaim(
    STREAM,
    GROUP,
    "worker-2",
    min_idle_time=60000,
    message_ids=["1705312800000-0", "1705312800001-0"],
)
```

#### Recovery Pattern: Process Pending Then New

```python
async def consumer_with_recovery(r, group: str, consumer_name: str):
    """Consumer that first processes its own pending messages, then reads new ones."""

    # Phase 1: Re-process any pending (unACKed) messages from a previous crash
    pending_id = "0"
    while True:
        results = await r.xreadgroup(
            groupname=group,
            consumername=consumer_name,
            streams={STREAM: pending_id},  # "0" = read pending messages
            count=10,
        )
        if not results or not results[0][1]:
            break  # no more pending messages

        for stream_name, messages in results:
            for msg_id, msg_data in messages:
                await process_event(msg_data)
                await r.xack(STREAM, group, msg_id)
                pending_id = msg_id

    # Phase 2: Read new messages
    while True:
        try:
            results = await r.xreadgroup(
                groupname=group,
                consumername=consumer_name,
                streams={STREAM: ">"},
                count=10,
                block=5000,
            )
            if results:
                for stream_name, messages in results:
                    for msg_id, msg_data in messages:
                        await process_event(msg_data)
                        await r.xack(STREAM, group, msg_id)
        except asyncio.CancelledError:
            break
```

### Reliable Event Processor with Dead Letter Queue

```python
async def reliable_event_processor(
    r,
    stream: str,
    group: str,
    consumer: str,
    handler,
    batch_size: int = 10,
    block_ms: int = 5000,
    max_retries: int = 3
):
    """
    A production-ready stream consumer with:
    - Batched reads
    - Individual message ACK
    - Failed message detection and dead letter queue
    - Graceful shutdown
    """
    # Ensure group exists
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise

    # First: process any pending messages (from previous crash)
    await _process_pending(r, stream, group, consumer, handler, max_retries)

    # Then: process new messages
    while True:
        try:
            result = await r.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=batch_size,
                block=block_ms
            )

            if not result:
                continue

            for stream_name, messages in result:
                for msg_id, data in messages:
                    try:
                        await handler(msg_id, data)
                        await r.xack(stream, group, msg_id)
                    except Exception as e:
                        print(f"Failed to process {msg_id}: {e}")
                        # Message stays in PEL, will be retried

        except asyncio.CancelledError:
            print(f"[{consumer}] Shutting down gracefully...")
            break

async def _process_pending(r, stream, group, consumer, handler, max_retries):
    """Process messages that were delivered but not ACKed (crash recovery)."""
    while True:
        pending = await r.xpending_range(
            name=stream, groupname=group,
            min="-", max="+", count=10,
            consumername=consumer
        )

        if not pending:
            break

        for msg_info in pending:
            msg_id = msg_info["message_id"]
            delivery_count = msg_info["times_delivered"]

            if delivery_count > max_retries:
                print(f"Message {msg_id} exceeded max retries, sending to dead letter")
                await r.xadd(f"{stream}:dead-letter", {"original_id": msg_id})
                await r.xack(stream, group, msg_id)
                continue

            # Re-read the message data
            messages = await r.xrange(stream, min=msg_id, max=msg_id)
            if messages:
                _, data = messages[0]
                try:
                    await handler(msg_id, data)
                    await r.xack(stream, group, msg_id)
                except Exception as e:
                    print(f"Retry failed for {msg_id}: {e}")

# Usage
async def handle_order_event(msg_id: str, data: dict):
    print(f"Processing order event {msg_id}: {data}")
    # your business logic here

async def main():
    r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)
    await reliable_event_processor(
        r,
        stream="events:orders",
        group="order-processors",
        consumer="worker-1",
        handler=handle_order_event
    )
    await r.aclose()
```

### Stream Trimming

Streams grow indefinitely unless you trim them:

```python
# Trim to exact max length
await r.xtrim(STREAM, maxlen=10000)

# Approximate trim (more efficient — Redis trims in macro-node chunks)
await r.xtrim(STREAM, maxlen=10000, approximate=True)

# Trim by minimum ID (remove entries older than a specific ID)
await r.xtrim(STREAM, minid="1679012345678-0")

# Auto-trim on XADD (recommended approach)
await r.xadd(STREAM, {"data": "value"}, maxlen=10000, approximate=True)
```

---

## Pub/Sub vs Streams: Detailed Comparison

| Feature | Pub/Sub | Streams |
|---------|---------|---------|
| **Persistence** | None. Message gone after delivery. | Yes. Entries persist until trimmed. |
| **Delivery guarantee** | At-most-once | At-least-once (with consumer groups + ACK) |
| **Message replay** | Impossible | Read from any ID, re-read old entries |
| **Consumer groups** | No. Every subscriber gets every message. | Yes. Messages distributed among group members. |
| **Acknowledgments** | No | Yes (`XACK`) |
| **Backpressure** | Slow subscriber = Redis memory grows | Consumer reads at its own pace |
| **Pattern matching** | `PSUBSCRIBE events:*` | No (subscribe to specific streams) |
| **Blocking read** | `SUBSCRIBE` (dedicated connection) | `XREAD BLOCK` / `XREADGROUP BLOCK` |
| **Fan-out** | Built-in (all subscribers get it) | Multiple consumer groups on same stream |
| **Throughput** | Very high (simple forwarding) | High (but write + store overhead) |
| **Memory usage** | Minimal (no storage) | Proportional to retained entries |
| **Use case** | Real-time broadcast, ephemeral events | Event sourcing, job queues, audit logs |

### Decision Guide

```
Do you need message persistence?
+-- No  --> Pub/Sub
+-- Yes
    +-- Do you need consumer groups (distribute work)?
    |   +-- No  --> Streams with XREAD
    |   +-- Yes --> Streams with XREADGROUP
    +-- Do you need massive scale (millions/sec)?
        +-- No  --> Redis Streams
        +-- Yes --> Consider Kafka
```

---

## Streams vs Kafka: Quick Comparison

If you are deciding between Redis Streams and Apache Kafka, here is the trade-off:

| Feature | Redis Streams | Apache Kafka |
|---------|---------------|--------------|
| **Setup complexity** | Minimal (Redis is probably already in your stack) | Significant (ZooKeeper/KRaft, brokers, schema registry) |
| **Throughput** | Hundreds of thousands msg/sec | Millions msg/sec |
| **Retention** | In-memory (bounded by RAM) | Disk-based (can retain indefinitely) |
| **Partitioning** | Single stream (no partitions) | Topics with N partitions |
| **Consumer groups** | Yes, with ACK | Yes, with offsets and rebalancing |
| **Ordering** | Per-stream total ordering (single node) | Per-partition ordering |
| **Replication** | Redis Cluster / Sentinel (async) | Built-in multi-broker replication (ISR) |
| **Exactly-once** | No (at-least-once with ACK) | Yes (with transactions) |
| **Schema enforcement** | None (field-value pairs) | Schema Registry (Avro, Protobuf) |
| **Ecosystem** | redis-py | confluent-kafka-python, KSQL, Connect |
| **Operational cost** | Low | High |

### When to Choose Each

**Choose Redis Streams when:**
- You already use Redis for caching/sessions
- Throughput needs are moderate (tens of thousands msg/sec)
- Retention window is hours/days, not months
- You want minimal operational complexity
- The data fits in memory

**Choose Kafka when:**
- You need massive throughput (millions msg/sec)
- You need long-term retention (weeks, months, or forever)
- You need exactly-once semantics
- You have multiple teams/services consuming the same events
- You need schema evolution and compatibility enforcement
- You already have a Kafka cluster

---

## Practical Patterns

### Pattern 1: Pub/Sub for Cache Invalidation

When one app instance updates data, all instances need to know:

```python
import redis.asyncio as aioredis
import asyncio
import json

INVALIDATION_CHANNEL = "cache:invalidate"

async def cache_invalidation_listener(r, local_cache: dict):
    """Listen for invalidation messages and clear local cache."""
    pubsub = r.pubsub()
    await pubsub.subscribe(INVALIDATION_CHANNEL)

    async for message in pubsub.listen():
        if message["type"] == "message":
            key = message["data"]
            local_cache.pop(key, None)
            print(f"Invalidated local cache key: {key}")

async def update_user(r, local_cache: dict, user_id: int, data: dict):
    """Update user in DB and invalidate cache across all instances."""
    # Update in database
    # await db.update_user(user_id, data)

    # Remove from local cache
    cache_key = f"user:{user_id}"
    local_cache.pop(cache_key, None)

    # Tell all instances to invalidate
    await r.publish(INVALIDATION_CHANNEL, cache_key)
```

### Pattern 2: Pub/Sub for WebSocket Fan-Out

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import redis.asyncio as aioredis
import json

app = FastAPI()

@app.websocket("/ws/chat/{room}")
async def websocket_chat(websocket: WebSocket, room: str):
    await websocket.accept()
    r = app.state.redis

    pubsub = r.pubsub()
    await pubsub.subscribe(f"chat:{room}")

    try:
        # Fan out: forward Redis messages to WebSocket
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(f"chat:{room}")
        await pubsub.aclose()

async def send_chat_message(r, room: str, user: str, text: str):
    """Send a message to a chat room (from any app instance)."""
    await r.publish(f"chat:{room}", json.dumps({
        "user": user,
        "text": text,
        "room": room
    }))
```

### Pattern 3: Streams for Activity Feed

```python
async def add_activity(r, user_id: int, action: str, details: dict):
    """Add an activity event to the user's activity stream."""
    stream_key = f"activity:{user_id}"
    await r.xadd(stream_key, {
        "action": action,
        **{k: str(v) for k, v in details.items()}
    }, maxlen=1000, approximate=True)  # keep last ~1000 activities

async def get_recent_activity(r, user_id: int, count: int = 20):
    """Get the most recent activities for a user."""
    stream_key = f"activity:{user_id}"
    entries = await r.xrevrange(stream_key, "+", "-", count=count)
    return [
        {"id": entry_id, **data}
        for entry_id, data in entries
    ]
```

### Pattern 4: Combined Pub/Sub + Streams in FastAPI

A practical example showing both Pub/Sub for real-time push and Streams for durable processing:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
import redis.asyncio as aioredis
import asyncio
import json

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = aioredis.Redis(
        host="localhost", port=6379, decode_responses=True,
    )

    # Start stream consumer in background
    consumer_task = asyncio.create_task(
        order_stream_consumer(app.state.redis)
    )

    yield

    consumer_task.cancel()
    await app.state.redis.aclose()

app = FastAPI(lifespan=lifespan)

@app.post("/orders")
async def create_order(order_id: int, user_id: int):
    r = app.state.redis

    # Publish to stream (durable — guaranteed processing)
    await r.xadd("events:orders", {
        "action": "created",
        "order_id": str(order_id),
        "user_id": str(user_id),
    })

    # Also notify via pub/sub (real-time, best effort)
    await r.publish(f"notifications:{user_id}", json.dumps({
        "message": f"Order {order_id} created",
    }))

    return {"order_id": order_id, "status": "created"}

async def order_stream_consumer(r: aioredis.Redis):
    """Background task processing order events from a stream."""
    group = "api-order-processors"
    consumer = "api-worker-1"

    try:
        await r.xgroup_create("events:orders", group, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise

    while True:
        try:
            results = await r.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={"events:orders": ">"},
                count=10,
                block=2000,
            )
            if results:
                for stream_name, messages in results:
                    for msg_id, msg_data in messages:
                        print(f"Processing order event: {msg_data}")
                        # ... business logic ...
                        await r.xack("events:orders", group, msg_id)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Stream consumer error: {e}")
            await asyncio.sleep(1)
```

---

## Common Mistakes

### Mistake 1: Using Pub/Sub When You Need Durability

```python
# BAD: Using Pub/Sub for job processing
# If the worker is down when the message is published, the job is lost
await r.publish("jobs", json.dumps(job_data))

# GOOD: Using Streams for job processing
await r.xadd("jobs:stream", job_data)
```

### Mistake 2: Not Acknowledging Stream Messages

```python
# BAD: Reading without ACK — messages pile up in pending list
result = await r.xreadgroup(groupname=GROUP, consumername="w1",
                             streams={STREAM: ">"}, count=10)
for stream, messages in result:
    for msg_id, data in messages:
        process(data)
        # forgot to XACK!

# GOOD: Always ACK after successful processing
for stream, messages in result:
    for msg_id, data in messages:
        process(data)
        await r.xack(STREAM, GROUP, msg_id)
```

### Mistake 3: Not Trimming Streams

```python
# BAD: Stream grows forever, eventually fills RAM
await r.xadd("events", {"data": "value"})

# GOOD: Trim on every add
await r.xadd("events", {"data": "value"}, maxlen=50000, approximate=True)
```

### Mistake 4: Using a Single Redis Connection for Pub/Sub and Commands

Pub/Sub puts the connection in a special "subscriber mode" where only Pub/Sub commands are allowed.

```python
# This works because redis-py uses connection pooling internally.
# r.pubsub() gets its own connection from the pool.
# But if you manually manage a single connection, you will get errors.

# GOOD: Use connection pooling (default behavior of redis-py)
r = aioredis.Redis(host="localhost", port=6379)  # has a pool
pubsub = r.pubsub()  # gets its own connection
await r.get("some_key")  # uses a different connection from pool
```
