# Redis Data Structures & Commands

> **Mental model**: Redis is not just a key-value store. It is a **data structure server** — think of it as having remote, in-memory data structures served over TCP. You get strings, hashes, lists, sets, sorted sets, streams, and more, each with atomic operations designed for specific access patterns.

---

## The Big Picture

Every value in Redis is stored under a **key** (a string). The key is how you address the value. The value's **type** determines which commands you can use on it.

```
Key (string)          Value (typed)
─────────────         ────────────────────────
"user:42:name"    --> String: "Alice"
"user:42"         --> Hash: {name: "Alice", email: "a@b.com"}
"queue:jobs"      --> List: ["job1", "job2", "job3"]
"tags:post:7"     --> Set: {"python", "redis", "backend"}
"leaderboard"     --> Sorted Set: {("alice", 1500), ("bob", 1200)}
"events:orders"   --> Stream: [{id: "1-0", data: {...}}, ...]
```

### Setup: Connecting with redis-py

All examples use `redis-py`, the standard Python Redis client.

```bash
pip install redis
```

```python
import redis

r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

# decode_responses=True returns strings instead of bytes
# Without it, r.get("key") returns b"value" instead of "value"

r.ping()  # True
```

> **Always use `decode_responses=True`** unless you are storing binary data. It saves you from `.decode()` calls everywhere.

---

## 1. Strings

The simplest Redis type. A string value can be an actual string, an integer, a float, or even raw binary (up to 512 MB). Every Redis key is itself a string; this section is about the **value** being a string.

### Core Commands

| Command | Description | Example |
|---------|-------------|---------|
| `SET key value` | Set a value | `SET user:1:name "Alice"` |
| `GET key` | Get a value | `GET user:1:name` --> `"Alice"` |
| `SETNX key value` | Set only if key does NOT exist | `SETNX lock:job1 "worker-1"` |
| `SET key value EX seconds` | Set with TTL | `SET session:abc token EX 3600` |
| `INCR key` | Atomic increment by 1 | `INCR page:views:homepage` |
| `INCRBY key n` | Atomic increment by n | `INCRBY user:1:balance 50` |
| `DECR key` | Atomic decrement by 1 | `DECR stock:item:42` |
| `MSET k1 v1 k2 v2` | Set multiple keys at once | `MSET a 1 b 2 c 3` |
| `MGET k1 k2 k3` | Get multiple keys at once | `MGET a b c` --> `["1", "2", "3"]` |
| `TTL key` | Seconds until key expires | `TTL session:abc` --> `3598` |
| `EXPIRE key seconds` | Set TTL on existing key | `EXPIRE session:abc 1800` |
| `DEL key` | Delete a key | `DEL session:abc` |

### Python Examples

```python
# Basic set/get
r.set("user:1:name", "Alice")
name = r.get("user:1:name")  # "Alice"

# Set with expiration (TTL)
r.set("session:abc123", "user_id:42", ex=3600)  # expires in 1 hour

# Check remaining TTL
r.ttl("session:abc123")  # 3598

# Atomic increment — perfect for counters
r.set("page:views:homepage", 0)
r.incr("page:views:homepage")  # 1
r.incr("page:views:homepage")  # 2
r.incrby("page:views:homepage", 10)  # 12

# SETNX — set only if not exists (used for distributed locks)
acquired = r.setnx("lock:send-email:42", "worker-1")
if acquired:
    # we got the lock
    r.expire("lock:send-email:42", 30)  # auto-release after 30s
    # do the work...
    r.delete("lock:send-email:42")

# Better: SET with NX and EX in one atomic command
acquired = r.set("lock:send-email:42", "worker-1", nx=True, ex=30)
if acquired:
    # we got the lock atomically with a TTL
    pass

# Bulk operations (reduces round trips)
r.mset({"key1": "val1", "key2": "val2", "key3": "val3"})
values = r.mget("key1", "key2", "key3")  # ["val1", "val2", "val3"]
```

### Use Cases

- **Caching simple values**: HTML fragments, serialized JSON, API responses.
- **Counters**: Page views, rate limits, inventory counts. `INCR` is atomic so concurrent requests are safe.
- **Sessions**: Store session tokens with TTL (`SETEX` or `SET ... EX`).
- **Distributed locks**: `SET key value NX EX 30` — atomic set-if-not-exists with auto-release TTL. If the holder crashes, the lock auto-expires.
- **Feature flags**: Simple on/off switches.

---

## 2. Hashes

A hash is a map of field-value pairs under a single key. Think of it as a Python `dict` stored in Redis. Perfect for representing objects.

```
Key: user:42
┌──────────────┬───────────────────┐
│ Field        │ Value             │
├──────────────┼───────────────────┤
│ name         │ Alice             │
│ email        │ alice@example.com │
│ login_count  │ 42                │
│ plan         │ premium           │
└──────────────┴───────────────────┘
```

### Core Commands

| Command | Description | Example |
|---------|-------------|---------|
| `HSET key field value` | Set one or more fields | `HSET user:42 name "Alice" age 30` |
| `HGET key field` | Get one field | `HGET user:42 name` --> `"Alice"` |
| `HMGET key f1 f2 ...` | Get multiple fields | `HMGET user:42 name email` |
| `HGETALL key` | Get all fields and values | `HGETALL user:42` --> `{name: "Alice", age: "30"}` |
| `HDEL key field` | Delete a field | `HDEL user:42 plan` |
| `HEXISTS key field` | Check if field exists | `HEXISTS user:42 email` --> `1` |
| `HINCRBY key field n` | Atomic increment a field | `HINCRBY user:42 login_count 1` |
| `HLEN key` | Number of fields | `HLEN user:42` --> `4` |

### Python Examples

```python
# Store an object
r.hset("user:42", mapping={
    "name": "Alice",
    "email": "alice@example.com",
    "login_count": 0,
    "plan": "premium"
})

# Get a single field — no need to fetch entire object
name = r.hget("user:42", "name")  # "Alice"

# Get multiple fields
name, email = r.hmget("user:42", "name", "email")  # ["Alice", "alice@example.com"]

# Get all fields
user = r.hgetall("user:42")
# {"name": "Alice", "email": "alice@example.com", "login_count": "0", "plan": "premium"}

# Atomic increment
r.hincrby("user:42", "login_count", 1)

# Delete a field
r.hdel("user:42", "plan")

# Check existence
r.hexists("user:42", "email")  # True
```

### Hashes vs JSON Strings for Objects

You have two choices for storing objects:

```python
# Option A: JSON string
import json
r.set("user:42", json.dumps({"name": "Alice", "email": "a@b.com"}))
user = json.loads(r.get("user:42"))

# Option B: Hash
r.hset("user:42", mapping={"name": "Alice", "email": "a@b.com"})
user = r.hgetall("user:42")
```

| | Hash | JSON String |
|---|------|-------------|
| **Partial read** | `HGET user:42 name` — reads one field | `GET user:42` — must deserialize entire JSON, extract field |
| **Partial write** | `HSET user:42 name "Bob"` — updates one field | `GET` + deserialize + modify + serialize + `SET` — read-modify-write cycle |
| **Atomic increment** | `HINCRBY user:42 views 1` — single command | Not possible without a transaction |
| **Memory (small hashes)** | Optimized ziplist encoding for small hashes | No special optimization |
| **Nested structures** | Not supported (values are flat strings) | Full JSON nesting |

**Rule of thumb**: Use hashes when you need to read or update individual fields without fetching the whole object. Use JSON strings when you always read/write the entire object and want to preserve complex nested structures.

### Use Cases

- **User profiles / session data**: Partial reads and writes without serialization overhead.
- **Object storage**: Any entity with named attributes (product details, config settings).
- **Per-field counters**: `HINCRBY` for tracking multiple metrics under one key.

---

## 3. Lists

An ordered sequence of strings, backed by a doubly-linked list. Supports push/pop from both ends, making it a natural queue or stack.

```
Key: queue:jobs

      <-- LPUSH                              RPUSH -->
┌──────┬──────┬──────┬──────┬──────┬──────┐
│ job5 │ job4 │ job3 │ job2 │ job1 │ job0 │
└──────┴──────┴──────┴──────┴──────┴──────┘
  HEAD                                 TAIL
      <-- LPOP                              RPOP -->
```

### Core Commands

| Command | Description | Example |
|---------|-------------|---------|
| `LPUSH key value` | Push to the left (head) | `LPUSH queue:jobs "job1"` |
| `RPUSH key value` | Push to the right (tail) | `RPUSH queue:jobs "job2"` |
| `LPOP key` | Pop from the left | `LPOP queue:jobs` --> `"job1"` |
| `RPOP key` | Pop from the right | `RPOP queue:jobs` --> `"job2"` |
| `BLPOP key timeout` | Blocking pop (waits for data) | `BLPOP queue:jobs 30` |
| `BRPOP key timeout` | Blocking pop from right | `BRPOP queue:jobs 30` |
| `LRANGE key start stop` | Get a range of elements | `LRANGE queue:jobs 0 -1` (all) |
| `LLEN key` | Length of list | `LLEN queue:jobs` --> `5` |
| `LINDEX key index` | Get element by index | `LINDEX queue:jobs 0` |
| `LTRIM key start stop` | Trim list to range | `LTRIM queue:jobs 0 99` |

### Python Examples

```python
# Simple queue (FIFO): push right, pop left
r.rpush("queue:emails", "email_1", "email_2", "email_3")

# Worker processes jobs from the left
job = r.lpop("queue:emails")  # "email_1"

# Blocking pop — waits up to 30 seconds for a new item
# Returns (key, value) tuple or None on timeout
result = r.blpop("queue:emails", timeout=30)
if result:
    key, job = result
    print(f"Got {job} from {key}")

# Get all items without popping
all_jobs = r.lrange("queue:emails", 0, -1)  # ["email_2", "email_3"]

# Use as a bounded log (keep last 100 entries)
r.lpush("log:api-requests", "GET /api/users 200 12ms")
r.ltrim("log:api-requests", 0, 99)  # keep only the 100 most recent

# Stack (LIFO): push left, pop left
r.lpush("stack:undo", "action_1")
r.lpush("stack:undo", "action_2")
last_action = r.lpop("stack:undo")  # "action_2"
```

### Lists as a Simple Job Queue

```python
import json

# Producer
def enqueue_job(r, queue_name: str, job_data: dict):
    r.rpush(queue_name, json.dumps(job_data))

enqueue_job(r, "queue:emails", {
    "to": "alice@example.com",
    "subject": "Welcome!",
    "template": "welcome_email"
})

# Consumer (blocking loop)
def worker(r, queue_name: str):
    print(f"Worker listening on {queue_name}...")
    while True:
        result = r.blpop(queue_name, timeout=5)
        if result is None:
            continue  # timeout, loop again
        _, raw = result
        job = json.loads(raw)
        print(f"Processing: {job}")
        # do the work...
```

### Use Cases

- **Job queues**: `LPUSH` to enqueue, `BRPOP` to dequeue. The blocking pop means workers sleep until work arrives — no polling.
- **Recent items / activity feeds**: Push new items, `LTRIM` to cap the list, `LRANGE` to read.
- **Stacks**: `LPUSH` + `LPOP` gives you LIFO behavior.
- **Bounded logs**: Push and trim in a pipeline to keep the last N entries.

> **Caveat**: Redis lists are a good primitive for simple queues, but they lack features like retries, dead-letter queues, and acknowledgments. For production job queues, consider Redis Streams (section 6) or dedicated tools (Celery, RQ, Dramatiq).

---

## 4. Sets

An unordered collection of unique strings. No duplicates, no ordering. Supports membership tests, intersections, unions, and differences — the same operations as Python's `set`.

### Core Commands

| Command | Description | Example |
|---------|-------------|---------|
| `SADD key member` | Add member(s) | `SADD tags:post:1 "python" "redis"` |
| `SMEMBERS key` | Get all members | `SMEMBERS tags:post:1` |
| `SISMEMBER key member` | Check membership | `SISMEMBER tags:post:1 "python"` --> `1` |
| `SCARD key` | Count members | `SCARD tags:post:1` --> `2` |
| `SREM key member` | Remove member | `SREM tags:post:1 "redis"` |
| `SINTER key1 key2` | Intersection | `SINTER tags:post:1 tags:post:2` |
| `SUNION key1 key2` | Union | `SUNION tags:post:1 tags:post:2` |
| `SDIFF key1 key2` | Difference (in key1 but not key2) | `SDIFF tags:post:1 tags:post:2` |
| `SRANDMEMBER key count` | Random member(s) | `SRANDMEMBER tags:post:1 2` |

### Python Examples

```python
# Tagging system
r.sadd("tags:post:1", "python", "redis", "backend")
r.sadd("tags:post:2", "python", "fastapi", "async")

# Get all tags for a post
tags = r.smembers("tags:post:1")  # {"python", "redis", "backend"}

# Check if tagged
r.sismember("tags:post:1", "python")  # True
r.sismember("tags:post:1", "java")    # False

# Find common tags between posts
common = r.sinter("tags:post:1", "tags:post:2")  # {"python"}

# Find all unique tags across posts
all_tags = r.sunion("tags:post:1", "tags:post:2")
# {"python", "redis", "backend", "fastapi", "async"}

# Unique visitors tracking
r.sadd("visitors:2026-03-27", "user:1", "user:2", "user:3", "user:1")
unique_count = r.scard("visitors:2026-03-27")  # 3 (user:1 not counted twice)

# Online presence
r.sadd("online:users", "user:42")
r.srem("online:users", "user:42")  # user went offline
is_online = r.sismember("online:users", "user:42")  # False

# Mutual friends
r.sadd("friends:alice", "bob", "charlie", "diana")
r.sadd("friends:bob", "alice", "charlie", "eve")
mutual = r.sinter("friends:alice", "friends:bob")  # {"charlie"}

# Friends of alice but not bob (friend suggestions for bob)
suggestions = r.sdiff("friends:alice", "friends:bob")  # {"diana"}
```

### Use Cases

- **Tags and categories**: Unique labels per item, with intersection/union queries.
- **Unique visitors**: `SADD` is idempotent, `SCARD` gives the count.
- **Online presence**: Track who is currently online.
- **Social features**: Mutual friends (`SINTER`), friend suggestions (`SDIFF`), followers, blocked users.
- **Deduplication**: Track processed IDs to avoid re-processing.
- **Feature flags per user**: `SISMEMBER "feature:dark-mode" "user:42"`.

---

## 5. Sorted Sets

Like sets (unique members), but each member has a **score** (a floating-point number). Members are ordered by score. This makes sorted sets the go-to structure for rankings, leaderboards, rate limiting, and anything that requires ordering.

```
Key: leaderboard

┌──────────┬───────┐
│ Member   │ Score │
├──────────┼───────┤
│ bob      │ 1200  │
│ alice    │ 1500  │
│ charlie  │ 1800  │
└──────────┴───────┘
  lowest score  -->  highest score
```

### Core Commands

| Command | Description | Example |
|---------|-------------|---------|
| `ZADD key score member` | Add/update with score | `ZADD leaderboard 1500 "alice"` |
| `ZSCORE key member` | Get score | `ZSCORE leaderboard "alice"` --> `1500` |
| `ZRANGE key start stop` | Get by rank (low to high) | `ZRANGE leaderboard 0 9` (top 10) |
| `ZREVRANGE key start stop` | Get by rank (high to low) | `ZREVRANGE leaderboard 0 2` |
| `ZRANGEBYSCORE key min max` | Get by score range | `ZRANGEBYSCORE leaderboard 1000 2000` |
| `ZINCRBY key amount member` | Increment score | `ZINCRBY leaderboard 100 "alice"` |
| `ZRANK key member` | Get rank (0-based, low to high) | `ZRANK leaderboard "alice"` |
| `ZREVRANK key member` | Get rank (high to low) | `ZREVRANK leaderboard "alice"` |
| `ZREM key member` | Remove member | `ZREM leaderboard "alice"` |
| `ZCARD key` | Count members | `ZCARD leaderboard` --> `100` |
| `ZREMRANGEBYSCORE key min max` | Remove by score range | `ZREMRANGEBYSCORE events 0 1609459200` |

### Python Examples

```python
# Leaderboard
r.zadd("leaderboard", {"alice": 1500, "bob": 1200, "charlie": 1800})

# Top 3 players (highest first)
top3 = r.zrevrange("leaderboard", 0, 2, withscores=True)
# [("charlie", 1800.0), ("alice", 1500.0), ("bob", 1200.0)]

# Update score (alice wins a game)
r.zincrby("leaderboard", 200, "alice")  # alice now has 1700

# Get rank (0-based, highest first)
rank = r.zrevrank("leaderboard", "alice")  # 1 (second place)

# Get score
score = r.zscore("leaderboard", "alice")  # 1700.0

# Players with score between 1000 and 2000
mid_tier = r.zrangebyscore("leaderboard", 1000, 2000, withscores=True)
```

### Sorted Sets for Rate Limiting (Sliding Window)

A powerful pattern: use timestamps as scores to implement a sliding window rate limiter.

```python
import time

def is_rate_limited(r, user_id: str, limit: int = 10, window_seconds: int = 60) -> bool:
    """Allow `limit` requests per `window_seconds` per user."""
    key = f"ratelimit:{user_id}"
    now = time.time()
    window_start = now - window_seconds

    # Start a pipeline for atomicity
    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)  # remove old entries
    pipe.zadd(key, {f"{now}": now})               # add current request
    pipe.zcard(key)                                # count requests in window
    pipe.expire(key, window_seconds)               # auto-cleanup
    results = pipe.execute()

    request_count = results[2]
    return request_count > limit

# Usage
if is_rate_limited(r, "user:42", limit=10, window_seconds=60):
    # return 429 Too Many Requests
    pass
```

### Sorted Sets for Priority Queues

Score = priority. Lower score = higher priority. Pop the lowest-score item.

```python
import json

# Enqueue with priority
r.zadd("queue:priority", {
    json.dumps({"task": "send_alert", "level": "critical"}): 0,        # urgent
    json.dumps({"task": "send_email", "to": "alice@example.com"}): 1,  # high priority
    json.dumps({"task": "generate_report", "id": 42}): 5,             # low priority
})

# Dequeue highest priority item (lowest score)
items = r.zrange("queue:priority", 0, 0)  # get first item
if items:
    task = items[0]
    r.zrem("queue:priority", task)  # remove it
    print(json.loads(task))
```

### Sorted Sets for Delayed Job Scheduling

Use score as the scheduled execution timestamp:

```python
import time
import json

# Schedule a job for 5 minutes from now
job = json.dumps({"type": "send_reminder", "user_id": 42})
run_at = time.time() + 300  # 5 minutes
r.zadd("queue:delayed", {job: run_at})

# Worker: poll for jobs that are ready
def process_due_jobs(r):
    while True:
        now = time.time()
        # Get jobs with score <= now (due for execution)
        jobs = r.zrangebyscore("queue:delayed", 0, now, start=0, num=10)
        for job_raw in jobs:
            # Remove atomically (only one worker processes it)
            if r.zrem("queue:delayed", job_raw):
                job = json.loads(job_raw)
                print(f"Processing: {job}")
        time.sleep(1)
```

### Use Cases

- **Leaderboards**: Ranked access, score updates, and range queries in O(log N).
- **Rate limiting (sliding window)**: Score = timestamp. Remove entries outside the window, count remaining.
- **Priority queues**: Score = priority. Pop the lowest-score item.
- **Delayed job scheduling**: Score = Unix timestamp. Poll for jobs with score <= now.
- **Time-series indexes**: Score = Unix timestamp. Range queries by time.
- **Autocomplete / ranked search results**: Score = relevance or frequency.

---

## 6. Streams

Streams are Redis's most powerful data structure for **event logging and messaging**. Think of a stream as an append-only log where each entry has a unique auto-generated ID (based on timestamp) and a set of field-value pairs.

```
Stream: events:orders

┌─────────────────────┬────────────────────────────────────┐
│ ID                  │ Fields                             │
├─────────────────────┼────────────────────────────────────┤
│ 1705312800000-0     │ type=created order_id=101          │
│ 1705312800001-0     │ type=paid order_id=101             │
│ 1705312800500-0     │ type=created order_id=102          │
│ 1705312801000-0     │ type=shipped order_id=101          │
└─────────────────────┴────────────────────────────────────┘
                      entries persist, can be read anytime
```

### Core Commands

| Command | Description | Example |
|---------|-------------|---------|
| `XADD key * field value` | Append entry (`*` = auto ID) | `XADD events * type "order" id "42"` |
| `XLEN key` | Count entries | `XLEN events` --> `1500` |
| `XRANGE key start end` | Read range | `XRANGE events - +` (all) |
| `XREVRANGE key end start` | Read range in reverse | `XREVRANGE events + - COUNT 10` |
| `XREAD COUNT n STREAMS key id` | Read new entries | `XREAD COUNT 10 STREAMS events 0` |
| `XREAD BLOCK ms STREAMS key $` | Blocking read (new entries only) | Waits for new data |
| `XGROUP CREATE key group id` | Create consumer group | `XGROUP CREATE events mygroup 0` |
| `XREADGROUP GROUP g consumer COUNT n STREAMS key >` | Read as consumer | Group-based consumption |
| `XACK key group id` | Acknowledge processed entry | `XACK events mygroup 1234-0` |
| `XTRIM key MAXLEN n` | Trim to max length | `XTRIM events MAXLEN 10000` |

### Python Examples

```python
# Add events to a stream
entry_id = r.xadd("events:orders", {
    "type": "order_created",
    "order_id": "42",
    "user_id": "7",
    "total": "99.99"
})
# entry_id is something like "1679012345678-0"

# Read all entries
entries = r.xrange("events:orders", "-", "+")
# [("1679012345678-0", {"type": "order_created", "order_id": "42", ...})]

# Read last 10 entries
recent = r.xrevrange("events:orders", "+", "-", count=10)

# Read only new entries (for polling)
entries = r.xread({"events:orders": "0"}, count=10)

# Blocking read — wait for new entries (up to 5 seconds)
entries = r.xread({"events:orders": "$"}, count=10, block=5000)

# Stream length
length = r.xlen("events:orders")

# Trim stream to prevent unbounded growth
r.xtrim("events:orders", maxlen=10000, approximate=True)
```

### Consumer Groups

Consumer groups let multiple workers process a stream cooperatively. Each message is delivered to **one consumer** in the group — no duplicate processing.

```
Stream: events:orders
    |
    +-- Consumer Group: "order-processors"
    |       +-- worker-1 (gets messages 1, 3, 5 ...)
    |       +-- worker-2 (gets messages 2, 4, 6 ...)
    |       (each message delivered to exactly ONE consumer in the group)
    |
    +-- Consumer Group: "analytics"
            +-- analyzer-1 (gets ALL messages independently)
            (different groups see ALL messages)
```

Key concepts:

- **Within a group**: Messages are distributed (each message goes to one consumer). This is load balancing.
- **Across groups**: Each group sees every message independently. This is fan-out.
- **Messages must be acknowledged** (`XACK`) — unacknowledged messages can be reclaimed.
- **Pending Entries List (PEL)**: Tracks messages delivered but not yet acknowledged. If a consumer crashes, another can reclaim its pending messages with `XCLAIM` or `XAUTOCLAIM`.

```python
# Create a consumer group (start from the beginning of the stream)
try:
    r.xgroup_create("events:orders", "order-processors", id="0", mkstream=True)
except redis.exceptions.ResponseError:
    pass  # group already exists

# Consumer reads messages assigned to it
entries = r.xreadgroup(
    groupname="order-processors",
    consumername="worker-1",
    streams={"events:orders": ">"},  # ">" means undelivered messages
    count=5,
    block=5000
)

# Process and acknowledge
for stream, messages in entries:
    for msg_id, data in messages:
        print(f"Processing {msg_id}: {data}")
        # do the work...
        r.xack("events:orders", "order-processors", msg_id)
```

### Use Cases

- **Event logs**: Append events, read by time range, trim old entries.
- **Durable message queues**: Unlike Pub/Sub, messages persist and can be replayed.
- **Multi-consumer processing**: Consumer groups distribute work across workers with delivery guarantees.
- **Activity streams**: Ordered, timestamped, queryable feeds.

> **Streams are covered in depth in [02_pubsub_and_streams.md](02_pubsub_and_streams.md)**, including consumer group patterns, failure recovery, and comparison with Pub/Sub and Kafka.

---

## 7. Key Expiration & Memory Management

Every key in Redis can have a TTL (time to live). When the TTL expires, the key is automatically deleted. This is fundamental to caching.

### Setting TTLs

```python
# Set TTL when creating the key
r.set("session:abc", "user_data", ex=3600)       # expires in 3600 seconds
r.set("session:abc", "user_data", px=5000)        # expires in 5000 milliseconds
r.set("session:abc", "user_data", exat=1735689600)  # expires at Unix timestamp

# Set TTL on an existing key
r.expire("session:abc", 1800)     # 30 minutes from now
r.pexpire("session:abc", 5000)    # 5 seconds from now (milliseconds)
r.expireat("session:abc", 1735689600)  # at specific Unix timestamp

# Check remaining TTL
r.ttl("session:abc")    # seconds remaining (-1 = no expiry, -2 = key doesn't exist)
r.pttl("session:abc")   # milliseconds remaining

# Remove TTL (make key persistent)
r.persist("session:abc")
```

### How Redis Expires Keys

Redis uses two mechanisms:

1. **Lazy expiration**: When a key is accessed, Redis checks if it is expired and deletes it.
2. **Active expiration**: Redis periodically samples random keys with TTLs and deletes expired ones (20 times per second, checks 20 random keys each time).

This means expired keys may linger briefly in memory until they are accessed or sampled. This is by design — it keeps performance predictable.

### Eviction Policies

When Redis reaches its configured `maxmemory`, it must decide what to evict. The policy is set in `redis.conf`:

```
maxmemory 2gb
maxmemory-policy allkeys-lru
```

| Policy | Evicts from | Strategy | Use case |
|--------|------------|----------|----------|
| `noeviction` | Nothing | Returns error on write | When you cannot afford data loss |
| `allkeys-lru` | All keys | Least Recently Used | **Pure cache (most common)** |
| `allkeys-lfu` | All keys | Least Frequently Used | Cache with hot/cold access patterns |
| `allkeys-random` | All keys | Random | When all keys are equally important |
| `volatile-lru` | Keys with TTL | Least Recently Used | Mix of cached and persistent data |
| `volatile-lfu` | Keys with TTL | Least Frequently Used | Same, frequency-based |
| `volatile-ttl` | Keys with TTL | Shortest TTL first | When near-expiry keys should go first |
| `volatile-random` | Keys with TTL | Random | Mixed workload, simple eviction |

**LRU vs LFU**: LRU evicts the key accessed longest ago. LFU tracks access frequency — a key accessed 1000 times per hour will not be evicted by a one-time scan of many cold keys. LFU is better for skewed workloads where a small set of keys handles most traffic.

**Recommendation**: Use `allkeys-lru` for pure caching. Use `volatile-lru` if some keys must never be evicted (they have no TTL set). Switch to `allkeys-lfu` if you notice hot keys being evicted by cold-key scans.

### Memory Inspection

```python
# Memory usage of a specific key (in bytes)
r.memory_usage("user:42")  # 128

# General info
r.info("memory")
# {
#   "used_memory_human": "1.52G",
#   "maxmemory_human": "2.00G",
#   "maxmemory_policy": "allkeys-lru",
#   ...
# }

# Count all keys
r.dbsize()  # 15234
```

---

## Key Naming Conventions

Redis has no built-in namespacing. Use a consistent naming convention to keep keys organized.

```
entity:id:field            # user:42:name
entity:id                  # user:42 (hash)
scope:entity:id            # cache:user:42
action:entity:id           # lock:order:99
queue:name                 # queue:emails
ratelimit:entity:id        # ratelimit:user:42
```

Conventions:

- Use colons `:` as separators (universal convention).
- Keep keys short but readable (Redis stores every key in memory).
- Prefix by purpose: `cache:`, `session:`, `lock:`, `queue:`, `ratelimit:`.
- Include the entity type and ID: `user:42`, `order:99`.

---

## Data Structure Selection Guide

| Problem | Data Structure | Why |
|---------|----------------|-----|
| Cache a value with TTL | String | Simple, one key-value pair |
| Store an object with updatable fields | Hash | Read/update individual fields |
| Job queue (FIFO) | List | `RPUSH` + `BLPOP` = blocking queue |
| Unique items, tags, membership | Set | Deduplication, set operations |
| Leaderboard, ranking | Sorted Set | Score-based ordering, rank queries |
| Rate limiting (sliding window) | Sorted Set | Timestamps as scores, range queries |
| Event log, message stream | Stream | Append-only, consumer groups, replay |
| Simple counter | String (`INCR`) | Atomic increment |
| Distributed lock | String (`SET NX EX`) | Atomic set-if-not-exists with TTL |
| Session storage | String or Hash | TTL-based, one per session |
| Recent activity feed | List (`LPUSH` + `LTRIM`) | Bounded, most-recent-first |
| Delayed job scheduling | Sorted Set | Score = Unix timestamp, poll for due jobs |
| Priority queue | Sorted Set | Score = priority, pop lowest score |

---

## Quick Reference: All Commands by Type

```
Strings:  SET  GET  SETNX  INCR  DECR  MSET  MGET  APPEND  STRLEN
Hashes:   HSET  HGET  HGETALL  HDEL  HEXISTS  HINCRBY  HLEN  HKEYS  HVALS
Lists:    LPUSH  RPUSH  LPOP  RPOP  BLPOP  BRPOP  LRANGE  LLEN  LINDEX  LTRIM
Sets:     SADD  SREM  SMEMBERS  SISMEMBER  SCARD  SINTER  SUNION  SDIFF  SRANDMEMBER
Sorted:   ZADD  ZSCORE  ZRANGE  ZREVRANGE  ZRANGEBYSCORE  ZINCRBY  ZRANK  ZREM  ZCARD
Streams:  XADD  XREAD  XRANGE  XLEN  XGROUP  XREADGROUP  XACK  XTRIM  XPENDING
Keys:     DEL  EXISTS  EXPIRE  TTL  PERSIST  TYPE  RENAME  SCAN  KEYS
Server:   PING  INFO  DBSIZE  FLUSHDB  CONFIG GET  MEMORY USAGE  SLOWLOG
```
