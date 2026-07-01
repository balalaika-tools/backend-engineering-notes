# Rate Limiting Algorithms

> **Why this lives here**: Rate limiting in a distributed system is fundamentally a Redis problem. Each algorithm is just a pattern of `INCR`, `EXPIRE`, or sorted-set commands — Redis provides the atomic primitives, you choose the key layout. This file covers the four canonical algorithms you'll actually use in production. The application-layer architecture that consumes these primitives lives in [Safe and Scalable API Calls / 09 — Distributed Admission Control](../../fundamentals/fastapi/safe_and_scalable_api_calls/09_distributed_admission_control.md).

---

## The Core Insight

Redis does not "calculate rates". Redis stores integers and sorted sets, and runs your Lua atomically. A *rate limiter* is a convention: you pick a key naming scheme so that arithmetic gives you the answer you want.

```
"60 requests per minute per user"
        ↓ becomes ↓
key = f"rpm:{user_id}:{minute_number}"
INCR that key, set TTL on first hit, reject if value > 60
```

The minute number changes every 60 seconds → a new key appears → the old one expires on its own. That is the entire algorithm. Everything below is a variation on this theme.

---

## Algorithm 1: Fixed Window

The simplest algorithm. Bucket time into fixed windows; count requests per bucket; reject when over limit.

### How it works

```
time:   12:00:00 ─── 12:00:59 │ 12:01:00 ─── 12:01:59 │ 12:02:00 ...
key:    rpm:user1:27891240    │ rpm:user1:27891241    │ rpm:user1:27891242
count:  [INCR each request]   │ [INCR each request]   │ ...
```

The window number is `floor(now / window_seconds)`. When `now` crosses a window boundary, you start writing to a new key. The old key expires automatically.

### Implementation

```python
import time
import redis.asyncio as aioredis
from typing import Optional

async def check_fixed_window(
    r: aioredis.Redis,
    key_prefix: str,
    limit: int,
    window_seconds: int = 60,
) -> bool:
    """
    Returns True if request is allowed, False if over limit.

    `key_prefix` should already encode the dimension you're limiting,
    e.g. "rpm:user:42" or "rpm:tier:pro".
    """
    window = int(time.time() // window_seconds)
    key = f"{key_prefix}:{window}"

    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, window_seconds + 10)  # +10s slack for clock skew
    count, _ = await pipe.execute()

    return count <= limit
```

### Why pipeline, not Lua?

`INCR` followed by `EXPIRE` in a pipeline is *not* atomic in the strict sense — another command could land between them — but it doesn't matter here because:

- `INCR` on a non-existent key returns `1`
- `EXPIRE` is idempotent — setting it twice on the same key is fine
- The TTL only matters for cleanup, not correctness

If you want strict atomicity (e.g. for the multi-dimension admission script in part 9), use Lua.

> **Note on the unconditional `EXPIRE`**: this snippet calls `EXPIRE` on every request rather than only on the first one (`count == 1`). That keeps the pipeline branch-free at the cost of one redundant `EXPIRE` per request. Because the key is window-pinned (a new key appears each window), the TTL doesn't drift meaningfully, so it's a fine trade for simplicity here. The "TTL Hygiene" section below explains when the first-INCR-only pattern is worth the extra round-trip.

### Trade-offs

✅ **Cheap**: one `INCR`, one `EXPIRE`, no sorted-set machinery.
✅ **Memory**: one integer per active window per key.
✅ **Easy to reason about**: count = number of requests in this minute.

❌ **Boundary burst**: this is the famous problem. A user can do 60 requests at `12:00:59.9`, then 60 more at `12:01:00.0`, totalling **120 requests in 100ms** while staying "within limit". For internal fairness this is usually fine. For protecting an upstream vendor with a hard cap, it's not.

### When to use

- Per-user fairness (boundary bursts are mostly cosmetic at this layer)
- Internal limits where small overshoot is acceptable
- Anywhere you'd choose simplicity over precision

---

## Algorithm 2: Sliding Window Log

Precise to the millisecond. Store every request timestamp in a sorted set; count entries in the last `window_seconds`.

### How it works

```
sorted set: rpm:user:42
  member="req-uuid-1"  score=1705312800.123
  member="req-uuid-2"  score=1705312805.671
  member="req-uuid-3"  score=1705312810.012
  ...

To check: drop entries with score < (now - window), count what's left.
```

### Implementation

```python
import time
import uuid
import redis.asyncio as aioredis

async def check_sliding_log(
    r: aioredis.Redis,
    key: str,
    limit: int,
    window_seconds: int = 60,
) -> bool:
    """
    Sorted-set sliding window log.
    Each request is one entry scored by its timestamp.
    """
    now = time.time()
    window_start = now - window_seconds

    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)        # drop expired
    pipe.zcard(key)                                     # count current
    pipe.zadd(key, {f"{uuid.uuid4()}": now})            # add this one
    pipe.expire(key, window_seconds + 10)
    _, count, _, _ = await pipe.execute()

    return count < limit
```

> **Note on ordering**: the snippet adds *before* checking, so a single request can never see itself — but if you prefer reject-without-recording (so a rejected request leaves no trace), check first then conditionally add.

### Trade-offs

✅ **Exact**: no boundary bursts, no approximation. The window slides continuously.
✅ **Precise rejection reason**: you know exactly when each request landed.

❌ **Memory grows with traffic**: every request is one sorted-set entry. 10,000 req/min/user = 10,000 entries per key. At scale this is the dominant cost.
❌ **More expensive operations**: `ZREMRANGEBYSCORE` and `ZADD` are O(log N), and the set can get large.

### When to use

- Hard external contracts where boundary bursts are unacceptable (vendor APIs with strict TPM)
- Low-volume keys where memory isn't a concern
- When you need to introspect *which* requests counted (e.g. debugging)

---

## Algorithm 3: Sliding Window Counter (Hybrid) — *the one Cloudflare uses*

The best of both. Store one counter per fixed window (cheap, like algorithm 1), but estimate a sliding rate by weighted-averaging the **current** window with the **previous** one.

### The intuition

If you're 30 seconds into the current minute, then a "true sliding 60-second window" looks like:

```
[ ─── previous minute ─── ][ ─── current minute ─── ]
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                       last 60s = (50% of prev) + (100% of current so far)
```

So: weight the previous window by `1 - progress` where `progress` is "how far into the current window are we, 0.0 to 1.0".

### Implementation

```python
import time
import redis.asyncio as aioredis

async def check_sliding_counter(
    r: aioredis.Redis,
    key_prefix: str,
    limit: int,
    window_seconds: int = 60,
) -> bool:
    now = time.time()
    current_window = int(now // window_seconds)
    previous_window = current_window - 1
    progress = (now % window_seconds) / window_seconds  # 0.0 .. 1.0

    cur_key = f"{key_prefix}:{current_window}"
    prev_key = f"{key_prefix}:{previous_window}"

    pipe = r.pipeline()
    pipe.get(cur_key)
    pipe.get(prev_key)
    cur_raw, prev_raw = await pipe.execute()

    current = int(cur_raw or 0)
    previous = int(prev_raw or 0)

    # Weighted estimate
    estimated = current + previous * (1 - progress)

    if estimated >= limit:
        return False

    # Allowed → increment current window
    pipe = r.pipeline()
    pipe.incr(cur_key)
    pipe.expire(cur_key, window_seconds * 2 + 10)  # keep 2 windows alive
    await pipe.execute()

    return True
```

### Why this is the production default

| Concern | Fixed window | Sliding log | Sliding counter |
|---------|-------------|-------------|-----------------|
| Memory per key | 1 int | N entries | 2 ints |
| Boundary burst | ❌ Yes (2× burst) | ✅ None | ✅ Bounded (~1.05×) |
| Cost per check | 1 op | O(log N) | 2 GETs + 1 INCR |
| Correctness | Approximate | Exact | Approximate but bounded |

The hybrid gives you ~95% of the precision of a true sliding window at ~5% of the cost. Cloudflare's [public writeup](https://blog.cloudflare.com/counting-things-a-lot-of-different-things/) describes the exact pattern and shows the error is bounded by `1 / (limit / window_seconds)` which is small for reasonable limits.

### Trade-offs

✅ **Bounded burst**: at worst slightly over limit at boundary, never 2×.
✅ **Cheap**: two integers per key, two GETs per check.
✅ **Production-grade**: this is what real edge platforms use.

❌ **Slightly approximate**: under tail traffic patterns the estimate can be off by a few percent. Not visible to users.
❌ **Three round-trips** if you split GET and INCR — combine in a single Lua script for one round-trip (see below).

### Atomic Lua version (single round-trip)

```lua
-- KEYS[1] = current window key
-- KEYS[2] = previous window key
-- ARGV[1] = limit
-- ARGV[2] = window_seconds
-- ARGV[3] = progress (0.0 .. 1.0)

local current = tonumber(redis.call("GET", KEYS[1]) or "0")
local previous = tonumber(redis.call("GET", KEYS[2]) or "0")
local estimated = current + previous * (1 - tonumber(ARGV[3]))

if estimated >= tonumber(ARGV[1]) then
  return 0  -- denied
end

local new = redis.call("INCR", KEYS[1])
if new == 1 then
  redis.call("EXPIRE", KEYS[1], tonumber(ARGV[2]) * 2 + 10)
end
return 1  -- allowed
```

```python
SLIDING_COUNTER_LUA = """<script above>"""

class SlidingCounter:
    def __init__(self, r: aioredis.Redis):
        self.r = r
        self._sha: Optional[str] = None

    async def _ensure_loaded(self) -> str:
        if self._sha is None:
            self._sha = await self.r.script_load(SLIDING_COUNTER_LUA)
        return self._sha

    async def check(self, key_prefix: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.time()
        current_window = int(now // window_seconds)
        previous_window = current_window - 1
        progress = (now % window_seconds) / window_seconds

        sha = await self._ensure_loaded()
        result = await self.r.evalsha(
            sha,
            2,
            f"{key_prefix}:{current_window}",
            f"{key_prefix}:{previous_window}",
            limit,
            window_seconds,
            progress,
        )
        return result == 1
```

`SCRIPT LOAD` once, `EVALSHA` forever — you avoid sending the Lua source on every call.

### When to use

- **Default choice for distributed rate limiting**. Pick this unless you have a specific reason to use one of the others.
- Per-user RPM, global RPM, vendor RPM, model RPM — anywhere with high traffic and the need for "pretty good" precision.

---

## Algorithm 4: Token Bucket

A different mental model: instead of *counting requests in a window*, you have a bucket that **refills at a fixed rate** and each request **consumes a token**.

### How it works

```
bucket starts full at capacity = 100 tokens
refill rate = 10 tokens/sec

Request arrives:
  1. compute tokens added since last check: (now - last) × refill_rate
  2. cap at capacity
  3. if tokens >= 1: deduct 1, allow
     else: deny
  4. store new (tokens, last) back in Redis
```

### Why use this instead of sliding counter?

Token bucket naturally supports **burstiness**: a user who's been idle accumulates tokens up to capacity, then can burst-spend them all at once. Sliding window says "no more than N in any 60s window" — token bucket says "average rate R, but you can burst up to B".

This matches how AWS API Gateway, Stripe, GitHub API, and most cloud providers expose their rate limits to clients.

### Implementation (Lua, atomic)

```lua
-- KEYS[1] = bucket key (stores hash with "tokens" and "ts")
-- ARGV[1] = capacity (max tokens)
-- ARGV[2] = refill_rate (tokens per second)
-- ARGV[3] = now (unix seconds, float)
-- ARGV[4] = cost (tokens this request consumes, usually 1)

local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call("HMGET", KEYS[1], "tokens", "ts")
local tokens = tonumber(data[1]) or capacity
local last = tonumber(data[2]) or now

-- Refill since last check
local elapsed = math.max(0, now - last)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

if tokens < cost then
  -- Save state (still update timestamp so refill clock advances)
  redis.call("HSET", KEYS[1], "tokens", tokens, "ts", now)
  redis.call("EXPIRE", KEYS[1], math.ceil(capacity / refill_rate) + 10)
  return 0  -- denied
end

tokens = tokens - cost
redis.call("HSET", KEYS[1], "tokens", tokens, "ts", now)
redis.call("EXPIRE", KEYS[1], math.ceil(capacity / refill_rate) + 10)
return 1  -- allowed
```

```python
TOKEN_BUCKET_LUA = """<script above>"""

class TokenBucket:
    def __init__(self, r: aioredis.Redis):
        self.r = r
        self._sha: Optional[str] = None

    async def consume(
        self,
        key: str,
        capacity: int,
        refill_rate: float,
        cost: int = 1,
    ) -> bool:
        if self._sha is None:
            self._sha = await self.r.script_load(TOKEN_BUCKET_LUA)
        result = await self.r.evalsha(
            self._sha, 1, key, capacity, refill_rate, time.time(), cost,
        )
        return result == 1
```

### Trade-offs

✅ **Naturally supports bursting**: idle users accumulate quota.
✅ **Variable cost**: consume `N` tokens for an expensive request (great for token quotas — see part 10 of the FastAPI section).
✅ **Self-cleaning**: TTL based on time-to-full ensures unused buckets evaporate.

❌ **State per key is bigger**: a hash with two fields, not a single integer.
❌ **Conceptually trickier**: you have to think in terms of capacity and refill rate, not "N per minute".

### When to use

- When the underlying contract is "rate R with burst B" — most external APIs.
- When request cost is **variable** (LLM token consumption, expensive vs cheap operations).
- When you want users to be able to burst after idle periods (better UX than strict windowing).

---

## Algorithm Selection Cheat Sheet

| Use case | Algorithm |
|----------|-----------|
| Per-user requests/min, internal | **Fixed window** (cheap, boundary burst is fine) |
| Per-user requests/min, customer-facing | **Sliding counter** (clean UX, no boundary bursts) |
| Vendor API with strict cap, low traffic | **Sliding log** (exact) |
| Vendor API with strict cap, high traffic | **Sliding counter** (cheap + bounded) |
| Variable-cost requests (LLM tokens, $) | **Token bucket** (cost = N tokens) |
| Match an external API's stated semantics | Whatever that API uses (usually token bucket) |

### The default

If you don't know which one to pick, pick **sliding counter**. It's the right answer 80% of the time.

---

## Concurrency Counters (Inflight)

A separate but related primitive: counting **in-flight** requests, not request **rate**. There is no window — just `INCR` on entry, `DECR` on exit.

```python
async def reserve_inflight(r: aioredis.Redis, key: str, limit: int) -> bool:
    """
    Returns True if a slot was reserved, False if the cap is full.
    Caller MUST decrement on exit.
    """
    new_count = await r.incr(key)
    if new_count > limit:
        await r.decr(key)
        return False
    return True

async def release_inflight(r: aioredis.Redis, key: str) -> None:
    await r.decr(key)
```

### Atomic version (Lua)

The non-atomic version above has a TOCTOU race: between `INCR` and the rollback `DECR`, another caller can see the inflated count. Usually harmless (the over-count drops back instantly), but if you care:

```lua
-- KEYS[1] = inflight key
-- ARGV[1] = limit
local n = redis.call("INCR", KEYS[1])
if n > tonumber(ARGV[1]) then
  redis.call("DECR", KEYS[1])
  return 0
end
return 1
```

### The leak problem

Counters don't auto-decrement. If a process crashes between `INCR` and `DECR`, the slot leaks forever. Two defences:

1. **TTL on the inflight key**: `EXPIRE inflight 60` — if no INCR/DECR activity for 60s, the key vanishes and resets the count. Imperfect but bounds the leak.
2. **Use a sorted set with timestamps** (heavier): each in-flight request is a member with `score = now + max_request_duration`. A periodic sweeper runs `ZREMRANGEBYSCORE` to drop expired entries. This is what production-grade implementations do.

For most apps the TTL approach is fine. Don't over-engineer.

---

## Multi-Dimensional Atomic Check (Preview)

Real systems check **many** keys per request: per-user RPM, per-user TPM, global RPM, global inflight, vendor RPM, paused-flags, banned-flags. Doing each one as a separate round-trip is slow *and* non-atomic.

The pattern is one Lua script that checks every dimension and returns a structured reason on rejection:

```lua
-- KEYS:  1=global_paused 2=user_banned 3=global_rpm 4=user_rpm 5=user_inflight
-- ARGV:  1=now 2=user_limit 3=global_limit 4=inflight_limit 5=window_seconds

if redis.call("GET", KEYS[1]) == "1" then return {0, "paused"} end
if redis.call("GET", KEYS[2]) == "1" then return {0, "banned"} end

local g = redis.call("INCR", KEYS[3])
if g == 1 then redis.call("EXPIRE", KEYS[3], tonumber(ARGV[5])) end
if g > tonumber(ARGV[3]) then return {0, "global_rpm"} end

local u = redis.call("INCR", KEYS[4])
if u == 1 then redis.call("EXPIRE", KEYS[4], tonumber(ARGV[5])) end
if u > tonumber(ARGV[2]) then return {0, "user_rpm"} end

local i = redis.call("INCR", KEYS[5])
if i > tonumber(ARGV[4]) then
  redis.call("DECR", KEYS[5])
  return {0, "inflight"}
end

return {1, "ok"}
```

This is the **admission controller** — application of these algorithms in the FastAPI layer. Full discussion with FastAPI integration in [09 — Distributed Admission Control](../../fundamentals/fastapi/safe_and_scalable_api_calls/09_distributed_admission_control.md).

---

## Cluster-Mode Hash Tags

If you're running Redis Cluster (multiple shards), keys are hashed to slots. A Lua script can only touch keys on a single slot. To force related keys onto the same shard, wrap the variable part in `{...}`:

```python
# Without hash tag — these may land on different shards:
"user:42:rpm"           → slot A
"user:42:tpm"           → slot B  ❌ Lua across both fails

# With hash tag — same slot guaranteed:
"user:{42}:rpm"         → slot derived from "42"
"user:{42}:tpm"         → same slot ✅ Lua works
```

Pick a hash-tag convention early. Renaming keys later is painful.

---

## TTL Hygiene

Every windowed key **must** have a TTL, or you slowly leak memory.

| Key type | TTL recommendation |
|----------|--------------------|
| Fixed window | `window_seconds + slack` (e.g. 60+10) |
| Sliding log | `window_seconds + slack` (sorted-set-wide TTL) |
| Sliding counter | `2 × window_seconds + slack` (need previous window alive) |
| Token bucket | `ceil(capacity / refill_rate) + slack` |
| Inflight counter | Short TTL (e.g. 60s) as a leak safety net |

Set TTL on **first INCR** (when the value equals 1) — not every request. Otherwise you're sending a redundant `EXPIRE` every time and burning CPU on the Redis server.

---

## What's Next

- For the architecture that uses these algorithms in a FastAPI service, see [Safe and Scalable API Calls / 09 — Distributed Admission Control](../../fundamentals/fastapi/safe_and_scalable_api_calls/09_distributed_admission_control.md).
- For the LLM-specific reservation/reconciliation pattern (token quotas with retries), see [Safe and Scalable API Calls / 10 — LLM Token Economics](../../fundamentals/fastapi/safe_and_scalable_api_calls/10_llm_token_economics.md).
