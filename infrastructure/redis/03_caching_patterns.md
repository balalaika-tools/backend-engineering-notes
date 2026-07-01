# Caching Patterns

> **Why cache?** A database query that takes 50ms under load becomes 0.5ms from Redis. Multiply that by thousands of requests per second and caching is the difference between a responsive app and one that buckles. Caching reduces latency, drops database load, and cuts infrastructure costs. The hard part is not caching itself — it is keeping the cache correct.

---

## Why Cache?

| Problem | How caching helps |
|---------|-------------------|
| High DB latency | Sub-millisecond reads from memory |
| Repeated identical queries | Serve once, cache many |
| Expensive computation | Compute once, reuse result |
| External API rate limits | Cache responses, reduce calls |
| Cost | Fewer DB connections, smaller instance needed |

---

## Caching Strategies

### 1. Cache-Aside (Lazy Loading)

The most common pattern. The application manages the cache explicitly.

```
Read path:
  App --> Cache (HIT?) --> yes --> return
                       --> no  --> DB --> write to Cache --> return

Write path:
  App --> DB --> delete Cache key
```

```python
import json
from typing import Optional
import redis.asyncio as aioredis

async def get_user(r: aioredis.Redis, user_id: str) -> dict:
    cache_key = f"user:{user_id}"

    # 1. Check cache
    cached = await r.get(cache_key)
    if cached:
        return json.loads(cached)

    # 2. Cache miss — fetch from DB
    user = await db.fetch_user(user_id)

    # 3. Populate cache (TTL = 1 hour)
    await r.setex(cache_key, 3600, json.dumps(user))

    return user

async def update_user(r: aioredis.Redis, user_id: str, data: dict):
    await db.update_user(user_id, data)
    # Invalidate cache — next read will re-populate
    await r.delete(f"user:{user_id}")
```

**Pros:** Only caches what is actually read. Simple to reason about. Cache failure is not fatal (just slower).
**Cons:** First request after miss is slow (cache miss penalty). Stale data possible if TTL is long and DB changes. Potential for cache stampede on hot keys.

#### Generic Cache-Aside Decorator

```python
import functools

def cache_aside(prefix: str, ttl: int = 300):
    """
    Decorator that implements cache-aside pattern.

    Usage:
        @cache_aside("user", ttl=600)
        async def get_user(user_id: int) -> dict:
            return await db.fetch_user(user_id)
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Build cache key from function name and arguments
            key_parts = [str(a) for a in args] + [f"{k}={v}" for k, v in sorted(kwargs.items())]
            cache_key = f"cache:{prefix}:{':'.join(key_parts)}"

            # Check cache
            cached = await async_redis.get(cache_key)
            if cached is not None:
                return json.loads(cached)

            # Cache miss — call function
            result = await func(*args, **kwargs)

            # Populate cache (don't cache None results)
            if result is not None:
                await async_redis.set(cache_key, json.dumps(result), ex=ttl)

            return result
        return wrapper
    return decorator

# Usage
@cache_aside("user", ttl=600)
async def get_user(user_id: int) -> dict:
    return await db.fetch_user(user_id)

@cache_aside("product", ttl=120)
async def get_product(product_id: int, include_reviews: bool = False) -> dict:
    return await db.fetch_product(product_id, include_reviews)
```

### 2. Write-Through

Every write goes to **both** cache and DB. Cache is always fresh.

```
Write path:
  App --> Cache + DB (simultaneously or sequentially)

Read path:
  App --> Cache (always HIT, in theory)
```

```python
async def update_user(r: aioredis.Redis, user_id: str, data: dict):
    await db.update_user(user_id, data)
    # Update cache immediately
    user = await db.fetch_user(user_id)
    await r.setex(f"user:{user_id}", 3600, json.dumps(user))

async def get_user(r: aioredis.Redis, user_id: str) -> dict:
    cache_key = f"user:{user_id}"

    # Should always be in cache if written through
    cached = await r.get(cache_key)
    if cached:
        return json.loads(cached)

    # Fall back to DB (cold start or eviction)
    user = await db.fetch_user(user_id)
    if user:
        await r.setex(cache_key, 3600, json.dumps(user))
    return user
```

**Pros:** Cache is always fresh. No stale reads.
**Cons:** Every write is slower (cache + DB). Caches data that may never be read. More complex write path.

### 3. Write-Behind (Write-Back)

Writes go to cache only. Cache flushes to DB asynchronously (in batches or on a timer).

```
Write path:
  App --> Cache (immediate return)
  Cache --> DB (async, batched)
```

```python
import asyncio

class WriteBehindCache:
    """
    Writes go to Redis immediately. A background task flushes
    dirty keys to the database periodically.
    """

    def __init__(self, redis_client, db, flush_interval: float = 1.0):
        self.r = redis_client
        self.db = db
        self.flush_interval = flush_interval
        self.dirty_keys: set[str] = set()
        self._lock = asyncio.Lock()

    async def write(self, user_id: int, data: dict):
        cache_key = f"cache:user:{user_id}"

        # Write to Redis immediately
        await self.r.set(cache_key, json.dumps(data), ex=600)

        # Mark key as dirty
        async with self._lock:
            self.dirty_keys.add(cache_key)

    async def flush_loop(self):
        """Background task that flushes dirty keys to DB."""
        while True:
            await asyncio.sleep(self.flush_interval)

            async with self._lock:
                keys_to_flush = self.dirty_keys.copy()
                self.dirty_keys.clear()

            for key in keys_to_flush:
                try:
                    data = await self.r.get(key)
                    if data:
                        user = json.loads(data)
                        await self.db.execute(
                            "UPDATE users SET name=%s, email=%s WHERE id=%s",
                            (user["name"], user["email"], user["id"]),
                        )
                except Exception as e:
                    # Re-add to dirty set for retry
                    async with self._lock:
                        self.dirty_keys.add(key)
                    print(f"Flush failed for {key}: {e}")
```

**Pros:** Extremely fast writes. Batch DB writes reduce load.
**Cons:** **Data loss risk** if Redis crashes before flush. Complex to implement correctly. Rarely used with Redis directly.

**Use sparingly**. Write-behind is suited for counters (page views, analytics) where losing a few data points is acceptable. Do not use it for financial transactions or anything requiring durability.

### 4. Read-Through

Cache itself fetches from DB on miss. The app never talks to the DB for reads.

```
Read path:
  App --> Cache --> (miss) --> Cache fetches from DB --> returns to App
```

This requires a cache layer that knows how to query your DB — usually a custom abstraction:

```python
class ReadThroughCache:
    """
    Application always calls cache.get(). On miss, the cache
    fetches from DB and populates itself.
    """

    def __init__(self, redis_client, default_ttl: int = 300):
        self.r = redis_client
        self.default_ttl = default_ttl
        self._loaders: dict[str, callable] = {}

    def register_loader(self, prefix: str, loader: callable):
        """Register a function that loads data from DB for a given prefix."""
        self._loaders[prefix] = loader

    async def get(self, prefix: str, key: str, ttl: Optional[int] = None) -> Optional[dict]:
        cache_key = f"cache:{prefix}:{key}"
        ttl = ttl or self.default_ttl

        # Check cache
        cached = await self.r.get(cache_key)
        if cached is not None:
            return json.loads(cached)

        # Cache miss — use registered loader
        loader = self._loaders.get(prefix)
        if loader is None:
            raise ValueError(f"No loader registered for prefix '{prefix}'")

        result = await loader(key)
        if result is not None:
            await self.r.set(cache_key, json.dumps(result), ex=ttl)

        return result

# Setup
cache = ReadThroughCache(redis_client)

async def load_user(user_id: str) -> Optional[dict]:
    return await db.fetch_user(int(user_id))

cache.register_loader("user", load_user)

# Usage — application never touches DB directly
user = await cache.get("user", "42")
```

**Cache-aside vs Read-through**: In cache-aside, the application code manages the cache. In read-through, the cache layer encapsulates the DB lookup. Read-through gives cleaner application code but less flexibility.

### Strategy Comparison

| Strategy | Read speed | Write speed | Freshness | Complexity |
|----------|-----------|------------|-----------|------------|
| Cache-Aside | Fast (hit) / Slow (miss) | Normal | Stale possible | Low |
| Write-Through | Always fast | Slower (2 writes) | Always fresh | Medium |
| Write-Behind | Always fast | Very fast | Eventually consistent | High |
| Read-Through | Fast (hit) / Slow (miss) | Normal | Stale possible | Medium |

```
Mental model:
Cache-aside     = "I'll check the cache myself"
Write-through   = "Always keep cache fresh on write"
Write-behind    = "Cache now, persist later"
Read-through    = "Cache fetches for me"
```

> **Rule of thumb:** Start with cache-aside. It handles 90% of cases. Move to write-through only if stale reads are unacceptable.

---

## TTL Strategies

### Fixed TTL

The simplest approach. Every cache entry gets the same expiration time.

```python
await r.setex("user:123", 3600, data)  # always 3600 seconds
```

### Sliding TTL

Refresh the TTL on every read. Frequently accessed data stays cached longer.

```python
cached = await r.get("user:123")
if cached:
    await r.expire("user:123", 3600)  # reset TTL on hit
```

### Adaptive TTL

Vary TTL based on the nature of the data. Static data gets long TTLs; volatile data gets short TTLs.

```python
TTL_CONFIG = {
    "user_profile": 600,       # 10 min — changes infrequently
    "user_preferences": 3600,  # 1 hour — almost never changes
    "product_price": 60,       # 1 min — can change often
    "stock_count": 10,         # 10 sec — highly volatile
    "static_page": 86400,      # 1 day — rarely changes
}

async def cache_with_adaptive_ttl(r, category: str, key: str, loader):
    cache_key = f"cache:{category}:{key}"
    ttl = TTL_CONFIG.get(category, 300)

    cached = await r.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    result = await loader()
    if result is not None:
        await r.set(cache_key, json.dumps(result), ex=ttl)
    return result
```

### How to Choose

| Strategy | How it works | Use when |
|----------|-------------|----------|
| **Fixed TTL** | `SET key value EX 3600` — always 3600s | Default choice. Predictable. |
| **Sliding TTL** | Reset TTL on every read (`EXPIRE key 3600`) | Frequently accessed data stays cached longer |
| **Adaptive TTL** | TTL based on data volatility (static = long, dynamic = short) | Mixed data patterns |
| **No TTL** | Explicit invalidation only | Data changes are always event-driven |

| Factor | Short TTL (< 60s) | Medium TTL (1-10 min) | Long TTL (> 10 min) |
|--------|-------------------|----------------------|---------------------|
| Data changes | Frequently | Occasionally | Rarely |
| Staleness tolerance | Low | Medium | High |
| Example | Stock count, live scores | User profiles, product details | Static content, config |
| DB load | Higher (more misses) | Balanced | Lower (fewer misses) |

---

## Eviction Policies

When Redis hits `maxmemory`, it must evict keys. Set the policy in `redis.conf`:

```
maxmemory 256mb
maxmemory-policy allkeys-lru
```

| Policy | Evicts from | Strategy | Use when |
|--------|------------|----------|----------|
| `noeviction` | Nothing | Returns error on write | You manage memory yourself |
| `allkeys-lru` | All keys | Least Recently Used | **Default choice for caching** |
| `volatile-lru` | Keys with TTL | Least Recently Used | Mix of cache + persistent keys |
| `allkeys-lfu` | All keys | Least Frequently Used | Some keys are much hotter than others |
| `volatile-lfu` | Keys with TTL | Least Frequently Used | Hot/cold split with persistent keys |
| `allkeys-random` | All keys | Random | Do not care which keys survive |
| `volatile-ttl` | Keys with TTL | Shortest TTL first | Prioritize longer-lived cache |
| `volatile-random` | Keys with TTL | Random | Mixed workload, simple eviction |

### LRU vs LFU

**LRU (Least Recently Used)**: Evicts the key that was accessed longest ago. Good general-purpose policy, but can be fooled by a one-time scan of many keys pushing out frequently accessed ones.

**LFU (Least Frequently Used)**: Tracks access frequency. A key accessed 1000 times per hour will not be evicted by a one-time scan. Better for skewed workloads where a small set of keys handles most traffic.

```
# redis.conf — LFU tuning
lfu-log-factor 10      # how fast the frequency counter grows (higher = slower)
lfu-decay-time 1       # minutes before frequency counter is halved (prevents stale hot keys)
```

> **Recommendation:** Use `allkeys-lru` for pure cache workloads. Use `volatile-lru` if Redis also stores non-cache data (sessions, queues). Switch to `allkeys-lfu` if you notice hot keys being evicted by cold-key scans (check `INFO stats` for `evicted_keys`).

---

## Cache Stampede (Thundering Herd)

### The Problem

A popular cache key expires. Hundreds of concurrent requests all see a cache miss simultaneously and all hit the database:

```
Time 0:     key "hot-data" expires
Time 0.001: Request A --> cache miss --> query DB
Time 0.002: Request B --> cache miss --> query DB
Time 0.003: Request C --> cache miss --> query DB
...
Time 0.050: 200 concurrent DB queries for the same data
```

### Solution 1: Mutex Lock (`SET ... NX EX`)

Only one request fetches from DB. Others wait and retry.

```python
import asyncio

async def get_with_lock(
    r: aioredis.Redis,
    key: str,
    fetch_fn,
    ttl: int = 3600,
    lock_ttl: int = 10,
) -> dict:
    # Try cache first
    cached = await r.get(key)
    if cached:
        return json.loads(cached)

    # Try to acquire lock
    lock_key = f"lock:{key}"
    acquired = await r.set(lock_key, "1", nx=True, ex=lock_ttl)

    if acquired:
        # We won the lock — fetch and populate
        try:
            value = await fetch_fn()
            await r.setex(key, ttl, json.dumps(value))
            return value
        finally:
            await r.delete(lock_key)
    else:
        # Someone else is fetching — wait briefly and retry
        for _ in range(50):  # max 5 seconds (50 * 0.1)
            await asyncio.sleep(0.1)
            cached = await r.get(key)
            if cached:
                return json.loads(cached)

        # Fallback: query DB directly (lock holder may have failed)
        return await fetch_fn()
```

### Aside — Single-Node `SET NX` vs RedLock

The mutex above is a **single-node** Redis lock: one Redis instance, one `SET NX`, one TTL. That's fine when losing the lock is a cache-stampede nuisance, not a correctness issue — the fallback path (fetch again) is safe.

For **distributed mutual exclusion** — "only one worker runs this side effect at a time, across the whole cluster" — there's a tempting algorithm called **RedLock** that uses multiple Redis instances. **Do not adopt RedLock without reading Martin Kleppmann's critique (["How to do distributed locking", 2016](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html)) and antirez's reply.** The short version of the dispute:

- RedLock assumes you can measure elapsed time accurately. **You cannot on a virtualized host** — the scheduler can pause a process for seconds. A process that acquired a lock can be paused until the TTL expires, resume, and happily do its work while another holder is also active.
- A GC pause in your application has the same effect. Between acquiring and doing the work, the JVM / Python can stall long enough for the lock to expire.
- Mitigation requires **fencing tokens** — the lock returns a monotonically-increasing token; every write to the protected resource includes the token; the resource rejects writes with a stale token. This means the resource itself participates in mutual exclusion; the "lock" is just a hint.

**Practical guidance for this corpus:**

- For **cache stampedes** (this section): single-node `SET NX` is fine; the downside is "more DB queries," not correctness.
- For **idempotency keys** (see [`safe_and_scalable_api_calls/11_idempotency.md`](../../fundamentals/fastapi/safe_and_scalable_api_calls/11_idempotency.md)): use a unique constraint in the database, not a Redis lock.
- For **"only one worker should run this cron"**: a single-node Redis lock with a short TTL and idempotent work is usually fine. If duplicate execution would be catastrophic, use a database-backed lock or a proper leader-election primitive (etcd, ZooKeeper, Consul).
- For **cross-region mutual exclusion where correctness matters**: Redis is the wrong tool. Use Postgres advisory locks, or a consensus system (etcd, ZooKeeper).

If you want a ready-made Redis lock, `redis-py` ships one: `r.lock(name, timeout=10, blocking_timeout=5)`. It is a single-node lock, not RedLock — fine for the use cases above, not a substitute for a real distributed-lock primitive.

### Solution 2: Probabilistic Early Expiration (XFetch)

Refresh the cache **before** it expires. Each request has a small probability of refreshing early, increasing as TTL approaches 0:

```python
import random

async def get_with_early_refresh(
    r: aioredis.Redis,
    key: str,
    fetch_fn,
    ttl: int = 3600,
    beta: float = 1.0,  # higher = more aggressive early refresh
):
    cached = await r.get(key)
    remaining_ttl = await r.ttl(key)

    if cached and remaining_ttl > 0:
        # Probabilistic early refresh
        # As TTL approaches 0, probability of refresh approaches 1
        if remaining_ttl > ttl * beta * random.random():
            return json.loads(cached)

    # Refresh (either expired or selected for early refresh)
    value = await fetch_fn()
    await r.setex(key, ttl, json.dumps(value))
    return value
```

### Solution 3: Background Refresh

Never let the cache expire. A background task refreshes before TTL runs out:

```python
class BackgroundCacheRefresher:
    """Refreshes cache keys in the background before they expire."""

    def __init__(self, redis_client, refresh_threshold: float = 0.2):
        self.r = redis_client
        self.refresh_threshold = refresh_threshold  # refresh when < 20% TTL remains
        self._loaders: dict[str, tuple[callable, int]] = {}

    def register(self, key: str, loader: callable, ttl: int):
        self._loaders[key] = (loader, ttl)

    async def refresh_loop(self):
        while True:
            for key, (loader, ttl) in self._loaders.items():
                cache_key = f"cache:{key}"
                remaining = await self.r.ttl(cache_key)

                if remaining == -2:
                    # Key does not exist — populate it
                    result = await loader()
                    if result is not None:
                        await self.r.set(cache_key, json.dumps(result), ex=ttl)

                elif remaining < ttl * self.refresh_threshold:
                    # TTL running low — refresh
                    result = await loader()
                    if result is not None:
                        await self.r.set(cache_key, json.dumps(result), ex=ttl)

            await asyncio.sleep(10)  # check every 10 seconds
```

### Which Anti-Stampede Approach to Use

| Approach | Complexity | Best for |
|----------|-----------|----------|
| **Mutex lock** | Low | Most applications. Simple and effective. |
| **XFetch** | Medium | High-throughput systems. No waiting, no locks. |
| **Background refresh** | Medium | Known set of hot keys. Proactive, no miss ever. |

---

## Cache Invalidation

> "There are only two hard things in Computer Science: cache invalidation and naming things." — Phil Karlton

### Delete on Write (Most Common)

The simplest approach. When you update the database, delete the corresponding cache key.

```python
async def update_user(r: aioredis.Redis, user_id: str, data: dict):
    await db.update_user(user_id, data)
    # Invalidate all related cache keys
    await r.delete(
        f"user:{user_id}",
        f"user:{user_id}:profile",
        f"user:{user_id}:settings",
    )
```

**Why delete, not update?** Deleting is simpler and avoids race conditions. If two writes happen concurrently:

```
With SET (update cache): race condition
Thread A: DB update (name="Alice")
Thread B: DB update (name="Bob")
Thread B: SET cache (name="Bob")
Thread A: SET cache (name="Alice")  <-- STALE! DB has "Bob" but cache has "Alice"

With DELETE: safe
Thread A: DB update (name="Alice")
Thread B: DB update (name="Bob")
Thread A: DELETE cache
Thread B: DELETE cache  <-- cache is empty, next read gets fresh "Bob" from DB
```

### Event-Driven with Pub/Sub

When you have multiple application servers, broadcast invalidation events:

```python
# Writer service
async def update_user(r: aioredis.Redis, user_id: str, data: dict):
    await db.update_user(user_id, data)
    await r.delete(f"user:{user_id}")
    await r.publish("cache:invalidate", json.dumps({"type": "user", "id": user_id}))

# Cache service (subscriber on each app server)
async def invalidation_listener(r: aioredis.Redis):
    pubsub = r.pubsub()
    await pubsub.subscribe("cache:invalidate")
    async for msg in pubsub.listen():
        if msg["type"] == "message":
            event = json.loads(msg["data"])
            if event["type"] == "user":
                await r.delete(f"user:{event['id']}")
                # Also clear local in-memory cache if you have one
```

### Versioned Keys

Instead of invalidating, change the cache key by including a version number. Old keys naturally expire via TTL.

```python
async def get_user_versioned(r, user_id: str) -> Optional[dict]:
    version = await r.get(f"version:user:{user_id}") or "v1"
    cache_key = f"user:{user_id}:{version}"

    cached = await r.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    user = await db.fetch_user(user_id)
    if user:
        await r.set(cache_key, json.dumps(user), ex=300)
    return user

async def update_user_versioned(r, user_id: str, data: dict):
    await db.update_user(user_id, data)
    # Bump version — old cache key becomes orphaned, expires via TTL
    await r.incr(f"version:user:{user_id}")
```

### Tag-Based Invalidation

Group related cache keys under tags. Invalidate all keys in a tag at once.

```python
async def cache_with_tags(r, cache_key: str, tags: list[str], data: str, ttl: int = 300):
    """Store data in cache and associate it with tags."""
    async with r.pipeline() as pipe:
        pipe.set(cache_key, data, ex=ttl)
        for tag in tags:
            pipe.sadd(f"tag:{tag}", cache_key)
            pipe.expire(f"tag:{tag}", ttl + 60)  # tag set outlives its members
        await pipe.execute()

async def invalidate_by_tag(r, tag: str):
    """Delete all cache keys associated with a tag."""
    cache_keys = await r.smembers(f"tag:{tag}")
    if cache_keys:
        async with r.pipeline() as pipe:
            for key in cache_keys:
                pipe.delete(key)
            pipe.delete(f"tag:{tag}")
            await pipe.execute()

# Usage
await cache_with_tags(r, "cache:product:1", ["products", "category:electronics"], product_json)
await cache_with_tags(r, "cache:product:2", ["products", "category:electronics"], product_json)
await cache_with_tags(r, "cache:product:3", ["products", "category:books"], product_json)

# Invalidate all electronics products
await invalidate_by_tag(r, "category:electronics")  # deletes product:1 and product:2
```

### Invalidation Strategy Comparison

| Strategy | How | Pros | Cons |
|----------|-----|------|------|
| **Delete on write** | `r.delete(key)` after DB write | Simple, correct | Cache miss after every write |
| **Update on write** | `r.setex(key, ...)` after DB write | No miss penalty | Write amplification, race conditions |
| **Event-driven** | Pub/sub or CDC triggers invalidation | Decoupled, works across services | More infrastructure |
| **Versioned keys** | `user:123:v5` — increment version on write | Old versions naturally expire | Key proliferation |
| **Tag-based** | Associate keys with tags, invalidate by tag | Bulk invalidation | Requires tracking structure |

---

## Caching Negative Results (Cache Miss Protection)

What happens when you query for data that does not exist? Without protection, every request for a non-existent user hits the database.

```python
async def get_user_with_negative_cache(r, user_id: str) -> Optional[dict]:
    cache_key = f"user:{user_id}"

    cached = await r.get(cache_key)
    if cached is not None:
        if cached == "__NULL__":
            return None  # cached negative result
        return json.loads(cached)

    user = await db.fetch_user(user_id)

    if user is not None:
        await r.set(cache_key, json.dumps(user), ex=300)
    else:
        # Cache the "not found" result with a shorter TTL
        await r.set(cache_key, "__NULL__", ex=60)

    return user
```

**Why a shorter TTL for negative results?** The data might be created soon. A 60-second TTL means at most 60 seconds of "not found" after creation, while still protecting the DB from repeated lookups of truly non-existent data.

---

## Summary

> Start with cache-aside + delete-on-write + `allkeys-lru`. Add complexity only when you have evidence you need it.
