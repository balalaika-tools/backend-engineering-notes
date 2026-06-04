# Python Redis Clients

> **Who this is for**: Engineers setting up Redis in a Python application -- choosing the right client, configuring connection pools, using pipelines, and integrating with FastAPI.

---

## The Landscape

The Python Redis ecosystem is simpler than you might expect:

| Library | What It Is |
|---|---|
| `redis-py` (sync) | The standard client. `redis.Redis()`. Blocking I/O. |
| `redis-py` (async) | Same library, async API. `redis.asyncio.Redis()`. |

> **That is it.** `redis-py` is the official client maintained by Redis. The separate `aioredis` package is now `redis.asyncio` within `redis-py` (merged as of redis-py 4.2.0, first shipped in 4.2.0rc1). If you see `import aioredis` in legacy code, replace it with `from redis import asyncio as aioredis` or `import redis.asyncio`.

```bash
pip install redis
```

> **Version note**: the examples here target `redis-py` 5.x, but the API shown (`redis.asyncio`, `from_url`, `aclose`, pipelines, Sentinel/Cluster) is unchanged through `redis-py` 8.x. The one behavioral change to know about: **`redis-py` 8.0 made RESP3 the default protocol** (5.x defaulted to RESP2). RESP3 changes how a few replies are typed (notably some commands return maps/`dict` instead of flat lists). If you upgrade and a parser breaks, pin the old behavior with `protocol=2` on the client, or update your reply handling.

---

## redis-py Sync Client

The synchronous client. Good for scripts, CLI tools, background workers, and any code that does not use asyncio.

```python
import redis

r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

# Basic operations
r.set("key", "value")
r.get("key")        # "value"
r.delete("key")
r.exists("key")     # 0

# From URL
r = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)

# With authentication
r = redis.Redis(host="redis.example.com", port=6379, password="secret", decode_responses=True)

# With TLS (e.g., AWS ElastiCache, Redis Cloud)
r = redis.Redis(host="redis.example.com", port=6380, password="secret", ssl=True, decode_responses=True)
```

### Type Mapping

| Redis Type | Python Type (decode_responses=True) | Python Type (decode_responses=False) |
|---|---|---|
| String | `str` | `bytes` |
| Integer (via INCR, etc.) | `int` | `int` |
| Float (via INCRBYFLOAT) | `float` | `float` |
| List | `list[str]` | `list[bytes]` |
| Hash | `dict[str, str]` | `dict[bytes, bytes]` |
| Set | `set[str]` | `set[bytes]` |
| None (key does not exist) | `None` | `None` |

> **Always use `decode_responses=True`** unless you are storing binary data (images, protobuf, etc.). It saves you from `.decode()` calls everywhere.

---

## redis-py Async Client

Same API, but with `await`. Use this in FastAPI, aiohttp, or any asyncio application.

```python
import redis.asyncio as aioredis

r = aioredis.Redis(host="localhost", port=6379, decode_responses=True)

await r.set("key", "value")
value = await r.get("key")  # "value"

# From URL
r = aioredis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)

# Always close when done (use aclose() in redis-py 5.x; close() is deprecated)
await r.aclose()
```

> **The async client is not thread-safe.** Create one client per asyncio event loop. In FastAPI, this means one client for the whole application (since FastAPI runs one event loop).

---

## Connection Pooling

### Why Pool?

Every Redis command needs a TCP connection. Without pooling, each command opens a new connection (TCP handshake + Redis AUTH + SELECT db). That is 1-3ms of overhead per command, which adds up fast.

A connection pool maintains a set of open connections and reuses them across commands.

```
Without pooling:                   With pooling:
Command 1 -> open conn -> close    Command 1 --+
Command 2 -> open conn -> close    Command 2 --+--> Pool (20 conns) --> Redis
Command 3 -> open conn -> close    Command 3 --+
(3 handshakes)                     (0 handshakes after warmup)
```

### Sync Connection Pool

```python
import redis

# Create a pool
pool = redis.ConnectionPool(
    host="localhost",
    port=6379,
    db=0,
    max_connections=20,
    decode_responses=True,
)

# Share the pool across your application
r = redis.Redis(connection_pool=pool)

# Each command borrows a connection from the pool and returns it when done
r.get("key")  # Borrows conn, executes GET, returns conn to pool
```

### Async Connection Pool

```python
import redis.asyncio as aioredis

# Create a pool
pool = aioredis.ConnectionPool.from_url(
    "redis://localhost:6379/0",
    max_connections=20,
    decode_responses=True,
)

r = aioredis.Redis(connection_pool=pool)

await r.get("key")

# Clean up
await r.aclose()
await pool.disconnect()
```

### BlockingConnectionPool

The default pool raises an error when all connections are in use. `BlockingConnectionPool` waits instead.

```python
pool = redis.BlockingConnectionPool(
    host="localhost",
    port=6379,
    max_connections=20,
    timeout=5,            # Wait up to 5 seconds for a connection
    decode_responses=True,
)
r = redis.Redis(connection_pool=pool)
```

### Pool Sizing

> **Rule of thumb**: Set `max_connections` to the number of concurrent Redis operations your app performs. For a web server with 10 worker threads, 10-20 connections is usually enough.

- Too few connections: requests wait for a connection (higher latency).
- Too many connections: Redis uses more memory per connection (~10KB each) and hits its `maxclients` limit.
- **Default**: `redis-py` creates a pool of 2^31 connections (effectively unlimited). Always set an explicit `max_connections` in production.

---

## Pipelines

### The Problem

Each Redis command is a network round trip (~0.1-1ms on localhost, 1-10ms over network). If you need to run 10 commands, that is 10 round trips.

### The Solution

A pipeline batches multiple commands into a **single round trip**.

```python
# Without pipeline: 3 round trips
await r.set("a", 1)      # Round trip 1
await r.set("b", 2)      # Round trip 2
value = await r.get("a")  # Round trip 3


# With pipeline: 1 round trip
async with r.pipeline(transaction=False) as pipe:
    pipe.set("a", 1)
    pipe.set("b", 2)
    pipe.get("a")
    results = await pipe.execute()  # [True, True, "1"]
```

### Sync Pipeline

```python
pipe = r.pipeline(transaction=False)
pipe.set("a", 1)
pipe.set("b", 2)
pipe.get("a")
results = pipe.execute()  # [True, True, "1"]
```

> **`transaction=False`** means commands are batched but not atomic. Use this for pure performance gains. Set `transaction=True` when you need atomicity (MULTI/EXEC).

### How Much Faster?

| Scenario | Without Pipeline | With Pipeline |
|---|---|---|
| 100 SET commands (localhost) | ~10ms | ~1ms |
| 100 SET commands (cross-AZ) | ~100ms | ~2ms |
| 1000 SET commands (cross-AZ) | ~1000ms | ~5ms |

> **Pipeline everything you can.** If you are doing multiple Redis operations in a single request handler, pipeline them.

---

## Transactions (MULTI/EXEC)

### Basic Transaction

A pipeline with `transaction=True` wraps commands in `MULTI/EXEC`. Either all commands execute or none do.

```python
async with r.pipeline(transaction=True) as pipe:
    pipe.set("balance:alice", 50)
    pipe.set("balance:bob", 150)
    results = await pipe.execute()  # Atomic: both set or neither
```

### Optimistic Locking with WATCH

`WATCH` monitors keys for changes. If any watched key is modified by another client before `EXEC`, the transaction aborts.

```python
async def transfer(r, from_user: str, to_user: str, amount: int):
    from_key = f"balance:{from_user}"
    to_key = f"balance:{to_user}"

    async with r.pipeline(transaction=True) as pipe:
        while True:
            try:
                # Watch the keys we are about to modify
                await pipe.watch(from_key, to_key)

                # Read current balances (outside transaction, while watching)
                from_balance = int(await pipe.get(from_key) or 0)
                to_balance = int(await pipe.get(to_key) or 0)

                if from_balance < amount:
                    await pipe.unwatch()
                    raise ValueError("Insufficient funds")

                # Start the transaction
                pipe.multi()
                pipe.set(from_key, from_balance - amount)
                pipe.set(to_key, to_balance + amount)

                # Execute -- if any watched key changed, this raises WatchError
                await pipe.execute()
                break

            except redis.WatchError:
                # Another client modified the key -- retry
                continue
```

> **WATCH + MULTI/EXEC** gives you optimistic concurrency control. It is the Redis equivalent of a compare-and-swap. Useful for counters, balances, and any read-modify-write operation.

### MULTI/EXEC vs Pipelines — They Are Not The Same Thing

The `redis-py` API puts both behind the same `pipeline()` method, but the semantics are different.

| | Pipeline (`transaction=False`) | Transaction / MULTI/EXEC (`transaction=True`) |
|---|---|---|
| What it does | Batches commands in one TCP round-trip | Batches **and** executes atomically on the server |
| Atomicity | None — a server crash mid-batch leaves partial state | All-or-nothing — either every command runs or none |
| Isolation | None — other clients can interleave writes | Commands run with no other client in between |
| Error behavior | Failed commands don't stop later ones | Syntax errors abort before EXEC; runtime errors (e.g. `INCR` on a string) leave already-queued commands to run; Redis returns the error in the result array |
| Use for | Pure throughput (one round-trip for N writes) | Read-modify-write, balance transfers, linked-key updates |

**Key subtleties:**

- A Redis "transaction" is **not a SQL transaction.** There's no rollback. If command 5 of 10 fails with a runtime error, commands 1–4 have already executed and 6–10 still will execute. You get the error in the results array — you do not get a rollback.
- `MULTI/EXEC` does **not** block other clients. Redis is single-threaded, so between MULTI and EXEC nothing else runs anyway — atomicity is a single-threaded-execution side effect, not a locking mechanism.
- `WATCH` is how you get isolation against other clients. Without WATCH, another client can change a key you read before your MULTI block, and you'll compute your updates on stale data. That's why the transfer example above uses `WATCH`.
- In **Redis Cluster**, all keys touched in a MULTI/EXEC must hash to the same slot — otherwise Redis rejects it with `CROSSSLOT`. Use hash tags (`balance:{user42}:available` / `balance:{user42}:pending`) to force them together.

**When to prefer Lua scripting over MULTI/EXEC:** if the operation involves conditional logic based on intermediate read results (e.g. "decrement if balance ≥ amount"), Lua scripts are simpler. The script runs atomically on the Redis server and you can branch inside it. See `EVAL` and `EVALSHA`.

---

## FastAPI Integration

### Recommended Pattern: Lifespan + Dependency Injection

```python
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
import redis.asyncio as aioredis


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create Redis connection pool
    app.state.redis = aioredis.Redis(
        host="localhost",
        port=6379,
        decode_responses=True,
        max_connections=20,
    )
    yield
    # Shutdown: close connections
    await app.state.redis.aclose()


app = FastAPI(lifespan=lifespan)


async def get_redis(request: Request) -> aioredis.Redis:
    """Dependency that provides the Redis client."""
    return request.app.state.redis


@app.get("/cached/{key}")
async def get_cached(key: str, r: aioredis.Redis = Depends(get_redis)):
    value = await r.get(key)
    return {"key": key, "value": value}


@app.post("/cached/{key}")
async def set_cached(key: str, value: str, r: aioredis.Redis = Depends(get_redis)):
    await r.set(key, value)
    return {"key": key, "value": value, "status": "ok"}
```

### Why This Pattern?

1. **One client for the app**: Created at startup, closed at shutdown. No per-request overhead.
2. **Dependency injection**: Route handlers declare `r: aioredis.Redis = Depends(get_redis)` -- easy to test (swap with a mock).
3. **Connection pooling**: The single client uses an internal pool. All routes share it.

### Health Check

```python
from fastapi import HTTPException

@app.get("/health")
async def health(r: aioredis.Redis = Depends(get_redis)):
    try:
        await r.ping()
        return {"redis": "ok"}
    except redis.ConnectionError:
        raise HTTPException(status_code=503, detail={"redis": "down"})
```

---

## Error Handling

### Exception Hierarchy

```
redis.RedisError
+-- redis.ConnectionError        # Cannot connect to Redis
|   +-- redis.TimeoutError       # Connection or command timed out
+-- redis.ResponseError          # Redis returned an error (e.g., wrong type)
+-- redis.WatchError             # WATCH key was modified (transaction aborted)
+-- redis.ReadOnlyError          # Write to a read-only replica
+-- redis.AuthenticationError    # Wrong password
```

### Practical Error Handling

```python
import redis
import redis.asyncio as aioredis


async def safe_get(r: aioredis.Redis, key: str, default=None):
    try:
        return await r.get(key)
    except redis.ConnectionError:
        # Redis is down -- degrade gracefully
        logger.warning(f"Redis connection failed for key={key}")
        return default
    except redis.TimeoutError:
        # Command timed out
        logger.warning(f"Redis timeout for key={key}")
        return default
    except redis.RedisError as e:
        # Catch-all for other Redis errors
        logger.error(f"Redis error for key={key}: {e}")
        return default
```

### Auto-Reconnect Behavior

`redis-py` automatically reconnects on the next command after a connection drops. You do not need to implement reconnection logic. However, the **command that was in-flight when the connection dropped will fail** -- catch that error and retry if needed.

### Retry Configuration

```python
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

retry = Retry(ExponentialBackoff(), retries=3)

r = aioredis.Redis(
    host="localhost",
    port=6379,
    decode_responses=True,
    retry=retry,
    retry_on_error=[redis.ConnectionError, redis.TimeoutError],
    socket_timeout=5,         # Timeout for individual commands
    socket_connect_timeout=5,  # Timeout for initial connection
)
```

> **Always set `socket_timeout`** in production. Without it, a network issue can cause your application to hang indefinitely waiting for a Redis response.

---

## Serialization

Redis stores everything as bytes. You need to serialize Python objects before storing and deserialize when reading.

### JSON (Most Common)

```python
import json

# Store
await r.set("user:42", json.dumps({"name": "Alice", "age": 30}))

# Retrieve
user = json.loads(await r.get("user:42"))
```

**Pros**: Human-readable, universal, debuggable with `redis-cli`.
**Cons**: Slower than binary formats, larger payload size.

### msgpack (Faster, Smaller)

```python
import msgpack

# Store
await r.set("user:42", msgpack.packb({"name": "Alice", "age": 30}))

# Retrieve (use raw_responses or decode_responses=False for binary)
user = msgpack.unpackb(await r.get("user:42"), raw=False)
```

**Pros**: 2-5x faster than JSON, 20-30% smaller payloads.
**Cons**: Not human-readable, need `decode_responses=False` for that key.

> **Note**: When using msgpack, you will need a separate Redis client with `decode_responses=False` since msgpack produces binary data. Or use a Redis Hash where some fields are text and store binary blobs separately.

### Pickle (Avoid in Production)

```python
import pickle

# DO NOT use this for untrusted data or across services
await r.set("user:42", pickle.dumps(user))
user = pickle.loads(await r.get("user:42"))
```

**Why avoid pickle**:
1. **Security**: `pickle.loads()` executes arbitrary code. If an attacker can write to your Redis, they can achieve remote code execution.
2. **Versioning**: Pickle format is tied to your Python version and class definitions. Schema changes break deserialization.
3. **Interoperability**: Only Python can read pickle. If another service (Go, Node) reads from the same Redis, it cannot deserialize.

> **Rule of thumb**: Use JSON for simplicity, msgpack when performance matters, and never pickle in production systems.

### Comparison

| Format | Speed | Size | Readable | Cross-language | Safe |
|---|---|---|---|---|---|
| JSON | Baseline | Baseline | Yes | Yes | Yes |
| msgpack | 2-5x faster | 20-30% smaller | No | Yes | Yes |
| pickle | ~1.5x faster | Varies | No | No | **No** |

---

## Quick Reference

```python
import redis.asyncio as aioredis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

# Production-ready client setup
r = aioredis.Redis(
    host="redis.example.com",
    port=6379,
    password="secret",
    db=0,
    decode_responses=True,
    max_connections=20,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry=Retry(ExponentialBackoff(), retries=3),
    retry_on_error=[ConnectionError, TimeoutError],
    ssl=True,  # Enable for cloud-hosted Redis
)
```
