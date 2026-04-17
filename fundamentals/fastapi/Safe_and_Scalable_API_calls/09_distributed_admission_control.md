# Part 9: Distributed Admission Control with Redis

> **Principle**: In a multi-pod system, your application is the brain and Redis is the shared memory. Every cross-pod limit reduces to "name a key, run a small Lua script, branch on the result". Fancy in-process libraries (`AsyncLimiter`, `aiolimiter`) protect a single process — useless when there are N pods.

> **Prerequisite**: Read [infrastructure/redis/05_rate_limiting.md](../../../infrastructure/redis/05_rate_limiting.md) for the four core algorithms (fixed window, sliding log, sliding counter, token bucket). This file uses them as building blocks.

---

## 1. Why "Redis + if statements" Is the Real Pattern

Parts 1–3 of this guide focused on the **transport layer**: the only hard concurrency limit is the HTTP client's socket pool. That's still true. But there's a second, equally important layer: **distributed admission control** — the application-layer decision of "should this request even start?".

For that decision, in-process tools are the wrong abstraction:

| Approach | Works across pods? | Flexible? | Debuggable? |
|----------|-------------------|-----------|-------------|
| `AsyncLimiter` (in-process) | ❌ Each pod has its own | ❌ Hard-coded windows | ✅ Trivial |
| Service mesh (Envoy / Istio rate-limit) | ✅ | ⚠️ Limited dimensions | ❌ Opaque |
| Managed (Upstash, Kong) | ✅ | ⚠️ Vendor-bound | ⚠️ |
| **Redis + Lua + `if` in your app** | ✅ | ✅ Any dimension | ✅ Just keys |

The "Redis + if" approach wins because:

1. **Every pod sees the same state instantly** — Redis is the single source of truth.
2. **You can model any dimension as a key** — per-user, per-tier, per-model, per-region, global, provider-side, kill switches. Same pattern, different keys.
3. **Atomic Lua** lets you check N dimensions in one round-trip, with a single rejection reason.
4. **Operational levers are first-class** — flipping `global:paused = 1` in Redis pauses all traffic instantly, no deploy.

> **Mental model**: Your backend is the brain. Redis is the shared cerebellum. Every container in every region asks the same Redis the same question and gets the same answer.

---

## 2. The Two Layers, Side by Side

```
┌─────────────────────────────────────────────────────────────┐
│ TRANSPORT LAYER (Parts 1–3)                                 │
│   - httpx connection limits                                 │
│   - timeouts (connect/read/write/pool, asyncio.timeout)     │
│   - in-process semaphore for *this pod's* socket usage      │
│ Scope: this pod, this process, this socket pool             │
└─────────────────────────────────────────────────────────────┘
                              ↑
                              │ this layer is downstream of admission
                              │
┌─────────────────────────────────────────────────────────────┐
│ ADMISSION CONTROL (this file, Part 9)                       │
│   - per-user RPM, TPM, daily quotas                         │
│   - per-tier / per-model fairness                           │
│   - global vendor protection                                │
│   - kill switches and overrides                             │
│ Scope: cluster-wide, shared via Redis                       │
└─────────────────────────────────────────────────────────────┘
                              ↑
                              │
                  ┌───────────────────────┐
                  │ Incoming HTTP request │
                  └───────────────────────┘
```

Both layers are needed. Admission control decides *whether* a request runs; the transport layer enforces what happens *if* it does. Skipping admission lets a single user starve everyone; skipping transport lets a single pod open infinite sockets and die.

---

## 3. The Redis Key Taxonomy

Pick the keys you'll actually use *before* writing code. Use a consistent prefix scheme so debugging is "scan keys by prefix and read the value".

### 3.1 Per-user limits (fairness)

| Key | Purpose | Algorithm |
|-----|---------|-----------|
| `user:{id}:rpm:{window}` | Requests per minute | Fixed window or sliding counter |
| `user:{id}:rph:{window}` | Requests per hour | Fixed window |
| `user:{id}:rpd:{window}` | Requests per day | Fixed window |
| `user:{id}:tpm:{window}` | **Tokens** per minute (LLM-specific) | Token bucket or sliding counter |
| `user:{id}:tpd:{window}` | Tokens per day | Fixed window with refund-on-error |
| `user:{id}:inflight` | Concurrent in-flight requests | Counter (no TTL, INCR/DECR pair) |
| `user:{id}:cost:{month}` | $ spent this month | Counter, accumulates floats |

### 3.2 Per-tier limits (monetization)

| Key | Purpose |
|-----|---------|
| `tier:free:rpm:{window}` | Shared pool for the entire free tier |
| `tier:pro:rpm:{window}` | Shared pool for pro tier |
| `tier:enterprise:rpm:{window}` | Shared pool for enterprise |

A tier-wide limit prevents one tier (usually free) from starving paid users when traffic spikes. Almost always combined with per-user limits.

### 3.3 Per-model limits (cost & vendor capacity)

| Key | Purpose |
|-----|---------|
| `user:{id}:model:{model}:rpm:{window}` | Limit expensive models per user |
| `user:{id}:model:{model}:tpd:{window}` | Daily token cap on a specific model |
| `model:{model}:inflight` | Global concurrency cap for one model |
| `model:{model}:rpm:{window}` | Global RPM for one model |

GPT-4 costs ~20× GPT-3.5. You probably don't want one quota across both — split by model and let users burn through cheap ones freely.

### 3.4 Global / infrastructure limits

| Key | Purpose |
|-----|---------|
| `global:inflight` | Total concurrent requests system-wide (your infra cap) |
| `global:rpm:{window}` | Total request rate (your infra cap) |
| `global:tpm:{window}` | Total token rate |
| `provider:{name}:rpm:{window}` | Track against the provider's stated RPM (OpenAI, Anthropic, …) |
| `provider:{name}:tpm:{window}` | Same, but for TPM (more important for LLMs) |
| `provider:{name}:inflight` | Concurrent calls to a specific provider |

**Provider keys exist to respect *their* contract**, not to protect your users. They are a property of the upstream relationship.

### 3.5 Control flags (kill switches)

| Key | Purpose |
|-----|---------|
| `global:paused` | `"1"` = stop all traffic (provider outage, maintenance) |
| `user:{id}:banned` | `"1"` = block this user |
| `model:{model}:paused` | `"1"` = disable a specific model |
| `tier:free:paused` | `"1"` = pause an entire tier |
| `feature:{name}:enabled` | Feature flag integrated into admission |

These are not rate limits — they're **boolean state**. Operations team flips them via `redis-cli SET global:paused 1` and traffic stops within milliseconds, no deploy.

### 3.6 Overrides & temporary bursts

| Key | Purpose |
|-----|---------|
| `user:{id}:rpm:override` | Boost a user's limit temporarily (TTL = duration) |
| `user:{id}:bonus_tokens:{day}` | Extra quota (promo, apology credit) |
| `user:{id}:trial_ends` | Trial expiration timestamp |

Customer-support tooling writes to these. The admission script reads `:override` first; falls back to the tier default if absent.

### 3.7 Observability (read-only — not used for limiting)

| Key | Purpose |
|-----|---------|
| `user:{id}:last_request` | Timestamp of last activity |
| `user:{id}:error_count:{window}` | User-caused errors |
| `user:{id}:429_count:{day}` | How often they hit the wall (signal for outreach) |
| `stats:rpm:{window}` | Time-series for dashboards |

---

## 4. Recommended Minimum Set

If you're starting fresh, do **not** implement all of section 3 on day one. These six keys cover ~90% of real needs:

```
1. user:{id}:rpm:{window}          → per-user fairness (requests)
2. user:{id}:tpd:{window}          → per-user fairness (cost)
3. user:{id}:inflight              → per-user concurrency
4. global:inflight                 → infra protection
5. provider:{name}:rpm:{window}    → respect provider's contract
6. global:paused                   → emergency kill switch
```

Add tier, model, and override keys as the product grows. Premature dimension proliferation is a real cost — every key is something you need to monitor, debug, and reason about.

---

## 5. The Atomic Admission Controller

The whole point of doing this in Redis is **one round-trip per request** that checks every dimension and returns a structured rejection reason. Anything else either races or is too slow.

### 5.1 The Lua script

```lua
-- KEYS:
--   1 = global_paused      (string flag)
--   2 = user_banned        (string flag)
--   3 = global_rpm         (counter, windowed)
--   4 = user_rpm           (counter, windowed)
--   5 = user_inflight      (counter, no TTL)
--   6 = provider_rpm       (counter, windowed)
--
-- ARGV:
--   1 = global_rpm_limit
--   2 = user_rpm_limit
--   3 = inflight_limit
--   4 = provider_rpm_limit
--   5 = window_seconds
--
-- Returns: { ok, reason, retry_after_seconds }
--   ok           = 1 if allowed, 0 if denied
--   reason       = string identifier of the limit hit
--   retry_after  = hint for client back-off (TTL on the offending key)

if redis.call("GET", KEYS[1]) == "1" then
  return {0, "paused", 30}
end
if redis.call("GET", KEYS[2]) == "1" then
  return {0, "banned", 0}
end

local window = tonumber(ARGV[5])

local g = redis.call("INCR", KEYS[3])
if g == 1 then redis.call("EXPIRE", KEYS[3], window) end
if g > tonumber(ARGV[1]) then
  return {0, "global_rpm", redis.call("TTL", KEYS[3])}
end

local u = redis.call("INCR", KEYS[4])
if u == 1 then redis.call("EXPIRE", KEYS[4], window) end
if u > tonumber(ARGV[2]) then
  return {0, "user_rpm", redis.call("TTL", KEYS[4])}
end

local p = redis.call("INCR", KEYS[6])
if p == 1 then redis.call("EXPIRE", KEYS[6], window) end
if p > tonumber(ARGV[4]) then
  return {0, "provider_rpm", redis.call("TTL", KEYS[6])}
end

local i = redis.call("INCR", KEYS[5])
if i > tonumber(ARGV[3]) then
  redis.call("DECR", KEYS[5])
  return {0, "user_inflight", 1}
end

return {1, "ok", 0}
```

**Why this order matters:**
- Flags first — cheapest checks, no `INCR` needed.
- Counters in increasing scope — global before user before inflight — so a global outage rejects fast and doesn't pollute per-user counters with rejected requests.
- `inflight` last because it's the only one that requires a rollback (`DECR` after over-limit `INCR`).

### 5.2 The Python wrapper

```python
import time
import redis.asyncio as aioredis
from dataclasses import dataclass
from typing import Optional

ADMISSION_LUA = """<script from 5.1>"""


@dataclass
class AdmissionResult:
    ok: bool
    reason: str
    retry_after: int  # seconds; 0 = no hint


class AdmissionController:
    """
    One Redis round-trip per request. Atomic across all checked dimensions.
    """

    def __init__(self, r: aioredis.Redis, provider: str = "openai"):
        self.r = r
        self.provider = provider
        self._sha: Optional[str] = None

    async def _ensure_loaded(self) -> str:
        if self._sha is None:
            self._sha = await self.r.script_load(ADMISSION_LUA)
        return self._sha

    async def check(
        self,
        user_id: str,
        global_rpm_limit: int,
        user_rpm_limit: int,
        inflight_limit: int,
        provider_rpm_limit: int,
        window_seconds: int = 60,
    ) -> AdmissionResult:
        window = int(time.time() // window_seconds)
        sha = await self._ensure_loaded()

        result = await self.r.evalsha(
            sha,
            6,
            "global:paused",
            f"user:{user_id}:banned",
            f"global:rpm:{window}",
            f"user:{user_id}:rpm:{window}",
            f"user:{user_id}:inflight",
            f"provider:{self.provider}:rpm:{window}",
            global_rpm_limit,
            user_rpm_limit,
            inflight_limit,
            provider_rpm_limit,
            window_seconds,
        )
        ok, reason, retry_after = result
        return AdmissionResult(
            ok=bool(ok),
            reason=reason.decode() if isinstance(reason, bytes) else reason,
            retry_after=int(retry_after),
        )

    async def release_inflight(self, user_id: str) -> None:
        """Always call in a `finally` block after the request completes."""
        await self.r.decr(f"user:{user_id}:inflight")
```

### 5.3 The FastAPI endpoint

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Response

admission: AdmissionController = None  # initialized at startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    global admission
    r = aioredis.from_url("redis://redis:6379", decode_responses=False)
    admission = AdmissionController(r, provider="openai")
    yield
    await r.aclose()


app = FastAPI(lifespan=lifespan)


@asynccontextmanager
async def reserve_admission(user_id: str, limits: dict):
    """
    Context manager: reserve a slot, guarantee release.
    """
    result = await admission.check(user_id=user_id, **limits)
    if not result.ok:
        raise HTTPException(
            status_code=429,
            detail={"reason": result.reason},
            headers={"Retry-After": str(result.retry_after)},
        )
    try:
        yield
    finally:
        await admission.release_inflight(user_id)


@app.post("/chat")
async def chat(request: ChatRequest, user=Depends(auth)):
    limits = await load_limits_for(user)  # tier-based, often cached locally

    async with reserve_admission(user.id, limits):
        # Inside this block: this request is *admitted*.
        # Now do the actual transport-layer work (Parts 1–3).
        return await call_llm(request)
```

That's it. No `AsyncLimiter`. No service mesh. Six keys, one Lua script, one context manager.

---

## 6. Retries and Quota Accounting (Critical)

This is the part most tutorials skip. Naively retrying inside the admission envelope causes either **double-counting** or **wrong quota state** — and the bug is silent until your largest customer's quota randomly says they're over when they're not.

### 6.1 Two perspectives on "1 request"

When an LLM call fails and retries:

```
User pressed "send" once
  ↓
Your backend tries: attempt 1 → 500 from provider
                    attempt 2 → success
```

From two perspectives, this is two different things:

| Perspective | What counts as one event |
|-------------|--------------------------|
| **The user's quota** | 1 request (they asked once) |
| **The provider's quota** | 2 requests (you called the API twice) |

If you increment `user:{id}:rpm` once per attempt, you're punishing the user for *your* retry policy. If you increment `provider:openai:rpm` only once, you'll silently exceed the provider's rate limit.

**Conclusion**: user-side and provider-side counters are different keys, incremented at different points.

### 6.2 The Reserve → Retry → Reconcile pattern

For LLM token quotas this becomes especially tricky because:

- You only know the *real* token cost **after** the response.
- You must *estimate* it up front to admit the request.
- A retry might consume tokens on the failed attempt (some providers bill for partial output before timing out).

The pattern:

```
1. RESERVE  — debit estimated cost up front
              (under one user quota, in the admission script)
2. RETRY    — internal retries against the provider
              (each attempt: increment provider:rpm/tpm)
3. RECONCILE — once per logical request (success OR final failure):
              compute (estimated - actual) and refund/charge the difference
              against the user quota
```

Code:

```python
@app.post("/chat")
async def chat(request: ChatRequest, user=Depends(auth)):
    limits = await load_limits_for(user)
    estimated_tokens = estimate(request)  # e.g. max_tokens or model-based heuristic

    # 1. ADMIT (request-count and inflight checked atomically)
    async with reserve_admission(user.id, limits):

        # 2. RESERVE the estimated tokens upfront so concurrent
        #    requests see the commitment. This is a separate INCRBY.
        await redis.incrby(f"user:{user.id}:tpd:{today()}", estimated_tokens)
        actual_tokens = 0
        succeeded = False

        try:
            # 3. RETRY internally. Each attempt is one provider call —
            #    increment provider counter per attempt, NOT per logical request.
            response = await call_llm_with_retry(
                request,
                on_attempt=lambda: redis.incr(f"provider:openai:rpm:{window()}"),
            )
            actual_tokens = response.usage.total_tokens
            succeeded = True
            return response

        finally:
            # 4. RECONCILE — exactly once.
            # If we reserved more than used, refund the difference.
            # If less (rare — estimate too low), we already charged the right keys.
            # If failed, refund the full reservation.
            if not succeeded:
                await redis.decrby(f"user:{user.id}:tpd:{today()}", estimated_tokens)
            else:
                diff = estimated_tokens - actual_tokens
                if diff != 0:
                    await redis.decrby(f"user:{user.id}:tpd:{today()}", diff)
```

### 6.3 Common mistakes

❌ **Incrementing user RPM inside the retry loop**
→ User's "1 request" charged 3× because of *your* retries. Confusing 429 errors.

❌ **Decrementing inflight before retries are done**
→ Another concurrent request slips into the slot. The user's effective concurrency is higher than configured.

❌ **Updating actual token count on each attempt**
→ Each retry double-deducts from the daily quota.

❌ **Not refunding on terminal failure**
→ A user's quota is consumed even though they got a 500 back. Will eventually mean their daily budget shows used = 800 when actual successful tokens = 200.

❌ **Estimating tokens as zero ("we'll just deduct after")**
→ Concurrent requests all see the user under quota, all get admitted, all consume the response budget at once. The user's daily limit is exceeded by N×.

### 6.4 What about "the failed attempt did consume tokens"?

Some LLM providers return usage data even on errors (e.g. you got partial output before a network timeout). If you can detect this, charge it on the way out:

```python
try:
    response = await call_llm(request)
except LLMError as e:
    # Some providers attach usage to errors
    if hasattr(e, "usage") and e.usage:
        # Charge the partial cost — separate from the reservation
        await redis.incrby(
            f"user:{user.id}:tpd:{today()}",
            e.usage.total_tokens,
        )
    raise
```

This goes *inside* the per-attempt loop, *outside* the reservation logic. The reservation is your "expected ceiling"; this is "actual partial cost incurred even though we failed".

### 6.5 The mental model

> **User-side quota = 1 reservation per logical request, reconciled exactly once.**
> **Provider-side quota = 1 increment per network attempt.**
> **Inflight slot = held for the entire reservation, including retries.**

The three counters move on different schedules. Conflating them is the source of every "why does this user's quota look weird" Slack thread.

---

## 7. Returning Useful 429 Responses

A bare `HTTPException(429)` is hostile. Clients have no idea when to retry, what they hit, or whether to back off exponentially.

```python
async with reserve_admission(user.id, limits) as admission:
    ...

# Inside reserve_admission, on rejection:
raise HTTPException(
    status_code=429,
    detail={
        "error": "rate_limit_exceeded",
        "reason": result.reason,         # "user_rpm" / "provider_rpm" / etc.
        "retry_after_seconds": result.retry_after,
    },
    headers={
        "Retry-After": str(result.retry_after),
        "X-RateLimit-Reason": result.reason,
        "X-RateLimit-Reset": str(int(time.time()) + result.retry_after),
    },
)
```

`Retry-After` is a [standard HTTP header](https://datatracker.ietf.org/doc/html/rfc7231#section-7.1.3). Real client SDKs (OpenAI's, Anthropic's, requests-with-tenacity) respect it automatically. Returning it for free turns a hostile 429 into a polite back-off signal.

---

## 8. Server-Side Retries vs Client-Side Back-off

Two completely different retry scenarios. Don't conflate them.

### 8.1 Server-side retry — *transient provider failure*

Your backend retries an LLM call after a 500 or timeout. The user knows nothing.

Use `tenacity`:

```python
from tenacity import (
    retry, stop_after_attempt, wait_exponential, retry_if_exception_type,
)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((TimeoutError, httpx.ConnectError)),
)
async def call_llm_with_retry(request):
    return await call_llm(request)
```

**Critical**: this happens *inside* the admission reservation (section 6). The user's inflight slot is held for the whole envelope. Don't release and re-acquire — another request will slip in.

### 8.2 Client-side back-off — *user is over their quota*

You returned a 429 with `Retry-After: 30`. The client (or its SDK) waits, then retries. Nothing for your backend to do — set the header correctly and move on.

### 8.3 Queue-and-retry — *hide 429s from users*

Sometimes you want to make rejection invisible: instead of 429, accept the request and process asynchronously when capacity exists.

```python
async with reserve_admission(user.id, limits, on_reject="queue") as r:
    if r.queued:
        await sqs.send_message(
            QueueUrl=queue,
            MessageBody=json.dumps(request.dict()),
            DelaySeconds=min(r.retry_after, 900),  # SQS max delay
        )
        return {"status": "queued", "job_id": job_id}
    # else: admitted, process synchronously
    return await call_llm(request)
```

A worker pool polls SQS, runs the same admission check, processes if allowed, re-queues with delay if not. This is the **admission control loop** at scale. Use it when:
- Your users tolerate eventual completion (batch jobs, agents, background work)
- You want amortized fairness over time, not strict real-time rejection
- You can afford the operational complexity of an async pipeline

Don't use it for synchronous user-facing chat. The user is sitting there waiting.

---

## 9. Operational Levers

These are the moments you'll be glad everything is in Redis.

### 9.1 Pause everything

```bash
redis-cli SET global:paused 1
# ... do whatever needs doing ...
redis-cli DEL global:paused
```

Every pod's next admission check returns `paused`. No deploy, no restart.

### 9.2 Ban a user

```bash
redis-cli SET user:abc123:banned 1
```

### 9.3 Boost a user temporarily

```bash
# 1000 RPM for the next hour (replaces tier default for this user)
redis-cli SET user:abc123:rpm:override 1000 EX 3600
```

In your admission script, check `:override` first, fall back to tier default. Customer success can issue these directly.

### 9.4 Disable a model

```bash
redis-cli SET model:gpt-4-turbo:paused 1
```

Useful when a specific model is having issues but others are fine.

### 9.5 Inspect a user's current state

```bash
redis-cli KEYS "user:abc123:*"
redis-cli MGET user:abc123:rpm:27891240 user:abc123:tpd:20260417 user:abc123:inflight
```

Debugging "why is this user getting 429s" is a `KEYS` away. Compare to debugging an in-process `AsyncLimiter` (you can't, it's per-pod state in someone's Python process).

---

## 10. Where AsyncLimiter Still Has a Role

This whole file argues against in-process libraries for **distributed admission**. They are still useful for:

- **Local burst smoothing** within a single pod, when you don't have Redis (e.g. unit tests, scripts)
- **Calling APIs from a CLI tool** where there's only one process
- **Smoothing internal tasks** that don't need cluster-wide coordination

If your service runs as more than one process, in-process limiters cannot enforce vendor or per-user limits. Use them as a *third* line of defence behind Redis admission, not as the primary control.

```python
# Belt and braces: Redis is authoritative, AsyncLimiter smooths within this pod
async with reserve_admission(user_id, limits):
    async with local_burst_smoother:  # AsyncLimiter, optional
        return await call_llm(request)
```

---

## 11. Where API Gateway Helps (and Where It Doesn't)

API Gateway native throttling (AWS API Gateway, Cloudflare, Kong, …) sits in front of your pods. It's perfect for:

| Use case | Why gateway is right |
|----------|----------------------|
| Raw flood / DDoS protection | Rejects before traffic hits your pods |
| Per-IP request count | No app context needed |
| Total RPS ceiling for the cluster | Single coarse number |
| Per-API-key limits | Most gateways do this natively |

Where it's not enough:

| Use case | Why gateway can't do it |
|----------|------------------------|
| Per-**user** limits when users share an API key (your frontend → your backend) | Gateway doesn't know the user |
| Token-based quotas | Token cost is in the response body — gateway never sees it |
| Tier-based limits with DB lookups | Gateway shouldn't talk to your DB |
| Reconciliation after retries | Stateful, app-level concern |

### Pattern: Lambda Authorizer + Redis at the edge

If you're on AWS API Gateway and want **per-user request count limits** rejected before traffic hits your container fleet:

```
Request
  → API Gateway (native throttle: total cluster RPS / DDoS)
    → Lambda Authorizer (user_id from JWT, INCR Redis key, allow/deny)
      → ECS / Lambda container (admission control + token quotas + LLM call)
```

The Lambda Authorizer does **only** request-count limiting (cheap, no response context). Token quotas, reconciliation, and inflight tracking still live in your app because they need post-response data.

Set `authorizer_result_ttl_in_seconds = 0` so each request gets a fresh check — caching defeats the point.

---

## 12. Summary

| Concern | Mechanism | Where |
|---------|-----------|-------|
| Raw flood / DDoS | Gateway native throttle | Edge |
| Per-IP limits | Gateway native throttle | Edge |
| Per-user request count | Redis (sliding counter) + Lambda Authorizer or app | Edge or app |
| Per-user token quota | Redis (reservation + reconciliation) | App |
| Per-user concurrency | Redis (`inflight` counter) | App |
| Tier / model fairness | Redis (per-tier, per-model keys) | App |
| Provider rate-limit compliance | Redis (`provider:*` counters, per attempt) | App |
| Kill switches / overrides | Redis flags | App, settable from CLI |
| Pod-level transport limits | `httpx` connection pool, semaphore | App (per-pod) |

### The two sentences

> **Use Redis + Lua + `if` statements as your distributed admission controller.** Reserve quota up front, reconcile once at the end; user counters move per logical request, provider counters move per network attempt; flags in Redis are your runtime levers.

---

**Next**: [Part 10: LLM Token Economics](10_llm_token_economics.md) — deep dive on the reserve/reconcile pattern for LLM-specific token quotas, including estimation strategies and partial-token error handling.
