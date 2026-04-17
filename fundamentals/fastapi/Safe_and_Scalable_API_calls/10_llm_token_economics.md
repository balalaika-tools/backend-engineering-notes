# Part 10: LLM Token Economics — Reserve, Retry, Reconcile

> **Principle**: For LLM calls the unit of cost is **tokens**, not requests. You only know the real token cost *after* the response. Quotas must be enforced *before* the call admits. Resolving that tension is what this file is about.

> **Prerequisites**:
> - [Part 9 — Distributed Admission Control](09_distributed_admission_control.md) for the Redis admission pattern.
> - [infrastructure/redis/05 — Rate Limiting Algorithms](../../../infrastructure/redis/05_rate_limiting.md) for the underlying counter primitives.

---

## 1. Why Token Quotas Are Different from Request Quotas

Request quotas have a clean shape:

```
Request arrives → check counter → INCR → allow or deny
```

Token quotas don't:

```
Request arrives → ??? we don't know the cost yet ??? → call LLM →
  response with usage.total_tokens → debit the actual amount
```

Three things go wrong if you naively "just debit after":

1. **Concurrent requests stampede.** N requests arrive simultaneously, each sees the user under quota, all are admitted. The user blows past the daily limit by N×.
2. **Failed calls escape accounting.** A timeout means no response → no `usage` data → no debit. The user can repeatedly fail expensive calls and never get charged.
3. **Retries double-count.** Each attempt that returns a response wants to debit usage. A request retried 3 times debits 3× the tokens.

The fix is the **Reserve → Retry → Reconcile** pattern.

---

## 2. The Pattern

```
┌──────────────────────────────────────────────────────────┐
│ 1. ESTIMATE   compute expected token cost up front       │
│                (max_tokens, model heuristic, prompt size)│
├──────────────────────────────────────────────────────────┤
│ 2. RESERVE    INCRBY estimated_cost on user's tpd key    │
│                — concurrent requests now see the debit   │
├──────────────────────────────────────────────────────────┤
│ 3. EXECUTE    call the LLM, retry transient failures     │
│                each network attempt → INCR provider:*    │
├──────────────────────────────────────────────────────────┤
│ 4. RECONCILE  exactly once, in a finally block:          │
│   on success:    diff = estimated - actual               │
│                  DECRBY diff (refund overestimate)       │
│   on failure:    DECRBY estimated_cost (full refund)     │
└──────────────────────────────────────────────────────────┘
```

The key invariant: **at any moment in Redis, the user's `tpd` counter reflects the real-or-pessimistically-estimated cost of every in-flight or completed request**.

---

## 3. Estimation Strategies

You need a number before the call. Three techniques, in order of accuracy:

### 3.1 Use `max_tokens` from the request

Easiest. If the request specifies a `max_tokens` cap:

```python
def estimate_tokens(request: ChatRequest) -> int:
    prompt_tokens = count_tokens(request.messages, model=request.model)
    completion_tokens = request.max_tokens or DEFAULT_MAX_OUTPUT
    return prompt_tokens + completion_tokens
```

Pros: deterministic, conservative (assumes worst-case output).
Cons: most responses are far shorter than `max_tokens` → big over-reservation → users hit their quota too soon during burst usage.

### 3.2 Use a model-specific heuristic

For each model, track the historical p95 output length per endpoint and estimate from that:

```python
P95_OUTPUT_TOKENS = {
    "gpt-4-turbo:/chat":    800,
    "gpt-4-turbo:/summary": 300,
    "gpt-3.5:/chat":       1200,
}

def estimate_tokens(request: ChatRequest) -> int:
    prompt = count_tokens(request.messages, model=request.model)
    output = P95_OUTPUT_TOKENS.get(f"{request.model}:{request.endpoint}", 500)
    return prompt + output
```

Pros: tighter than `max_tokens`, less over-reservation.
Cons: needs telemetry to keep the table accurate; users can blow past it (in which case reconciliation will *charge* the difference).

### 3.3 Reserve a small floor, charge the rest after

Reserve only enough to handle stampede protection (e.g. `prompt_tokens + 100`) and let reconciliation handle the actual amount. Works only if you're OK with brief over-spend.

```python
def estimate_tokens(request: ChatRequest) -> int:
    return count_tokens(request.messages, model=request.model) + 100
```

Pros: best UX (users rarely see false-positive 429s).
Cons: under high concurrency a user can briefly exceed quota — fine for soft limits, not for hard caps.

### 3.4 Recommendation

Start with **#1 (max_tokens)** — boring, correct, occasional over-reservation. Move to **#2 (heuristic)** once you have logging in place to maintain the p95 table. Use **#3** only for trusted users (paid tiers) where you'd rather be slightly generous than reject them.

---

## 4. The Implementation

```python
import time
import asyncio
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional, Callable, Awaitable
import redis.asyncio as aioredis
from fastapi import HTTPException


def today_key() -> str:
    return date.today().isoformat()  # "2026-04-17"


@asynccontextmanager
async def reserve_tokens(
    r: aioredis.Redis,
    user_id: str,
    estimated_tokens: int,
    daily_limit: int,
):
    """
    Reserve estimated_tokens against the user's daily quota.
    On exit, the caller MUST set actual_tokens via the yielded callback,
    or the full reservation is refunded (treated as failure).
    """
    key = f"user:{user_id}:tpd:{today_key()}"

    # 1. RESERVE — atomic check-and-increment
    new_total = await r.incrby(key, estimated_tokens)

    # First time today? Set TTL to roll over at midnight + slack
    if new_total == estimated_tokens:
        # 2 days TTL, plenty of slack — the date in the key handles real rollover
        await r.expire(key, 86400 * 2)

    if new_total > daily_limit:
        # Roll back our reservation
        await r.decrby(key, estimated_tokens)
        raise HTTPException(
            status_code=429,
            detail={
                "reason": "daily_token_quota_exceeded",
                "limit": daily_limit,
                "estimated_cost": estimated_tokens,
            },
            headers={"Retry-After": str(seconds_until_midnight())},
        )

    # 2. CALLER's WORK happens here. They tell us actual via the callback.
    state: dict = {"actual": None, "succeeded": False}

    def report(actual_tokens: int) -> None:
        state["actual"] = actual_tokens
        state["succeeded"] = True

    try:
        yield report
    finally:
        # 3. RECONCILE — exactly once.
        if state["succeeded"] and state["actual"] is not None:
            diff = estimated_tokens - state["actual"]
            if diff != 0:
                # diff > 0 → refund unused; diff < 0 → charge extra
                await r.decrby(key, diff)
        else:
            # Failure (exception or no report) → refund full reservation
            await r.decrby(key, estimated_tokens)
```

### Usage

```python
@app.post("/chat")
async def chat(request: ChatRequest, user=Depends(auth)):
    estimated = estimate_tokens(request)
    daily_limit = await load_daily_limit_for(user)

    async with reserve_tokens(redis, user.id, estimated, daily_limit) as report:
        response = await call_llm_with_retry(request)
        report(response.usage.total_tokens)   # ← reconciliation data
        return response
```

If `call_llm_with_retry` raises, `report` is never called → the `finally` block refunds the full reservation. If it succeeds, `report` records actual usage → `finally` refunds only the difference.

---

## 5. Provider-Side Counters Move on a Different Schedule

Re-read this if it's the only thing you remember:

> **User-side token counter: 1 reservation per logical request, reconciled once.**
> **Provider-side token counter: 1 increment per network attempt, with the actual usage from that attempt.**

A retried request looks like this in Redis:

```
User counter:        ─────RESERVE 1500──────────────────[reconcile to 800]─→
Provider counter:    ──+1200(attempt1 timeout, partial)──+800(attempt2 ok)──→
```

Provider counters exist to **respect the provider's rate limit**, which counts every actual API call regardless of your retry policy. User counters exist to **bill the user fairly**, which counts logical requests.

```python
async def call_llm_with_retry(request, redis, provider="openai"):
    last_error = None

    for attempt in range(3):
        # Per-attempt: increment provider counters
        window = int(time.time() // 60)
        await redis.incr(f"provider:{provider}:rpm:{window}")

        try:
            response = await call_llm(request)
            # On success, also charge actual tokens to provider TPM
            await redis.incrby(
                f"provider:{provider}:tpm:{window}",
                response.usage.total_tokens,
            )
            return response

        except LLMError as e:
            # Some providers attach usage even on errors (partial output billed)
            if hasattr(e, "usage") and e.usage:
                await redis.incrby(
                    f"provider:{provider}:tpm:{window}",
                    e.usage.total_tokens,
                )
            last_error = e
            if attempt < 2 and is_transient(e):
                await asyncio.sleep(2 ** attempt)
                continue
            raise

    raise last_error
```

The user-side `reserve_tokens` context manager wraps this. It debits the user once with the estimate, then reconciles once with the *final* `usage.total_tokens` of whichever attempt succeeded. If they all fail, full refund.

---

## 6. Edge Cases You Will Actually Hit

### 6.1 Streaming responses

For SSE / streaming LLM responses, `usage.total_tokens` arrives in the **last chunk** (or sometimes a separate "usage event"). Reconciliation has to wait until the stream ends.

```python
async def stream_chat(request, user):
    estimated = estimate_tokens(request)

    async with reserve_tokens(redis, user.id, estimated, daily_limit) as report:
        actual_total = 0

        async for chunk in call_llm_streaming(request):
            yield chunk
            if chunk.usage:  # final chunk
                actual_total = chunk.usage.total_tokens

        report(actual_total)
```

If the stream is cancelled mid-flight (client disconnect), `actual_total` is whatever you've counted so far — which may be zero if usage only arrives in the last chunk. In that case the `finally` block refunds the full reservation, which is wrong (you *did* spend tokens). Two mitigations:

- **Count tokens client-side as chunks arrive** using the model's tokenizer, so you have a running estimate even on disconnect.
- **Subscribe to provider's "usage on cancel" callbacks** if available (OpenAI streams `usage` in the last chunk; some providers send it on abort too).

See [Part 8 — Streaming Advanced](08_streaming_advanced.md) for streaming-specific concerns.

### 6.2 Partial output before error

OpenAI and Anthropic sometimes return a response with `finish_reason="length"` or `"content_filter"` — a successful API call that didn't deliver what the user asked for. From a quota perspective: **these are normal, billable responses**. Reconcile against `usage.total_tokens` as usual. Don't refund just because the user didn't get a great answer.

### 6.3 Hard daily limits with a long-running request

User has 1000 tokens left. They submit a prompt that estimates 800 tokens. Admitted. Mid-flight, another request from the same user arrives estimating 500 tokens. Rejected (only 200 left).

This is correct — but the rejected user might wonder why their quota dashboard says 800/1000 used while *their* request gets a 429. The answer: a sibling request reserved against the same quota.

**Communicate this clearly in the 429 body:**

```python
{
    "reason": "daily_token_quota_exceeded",
    "limit": 1000,
    "currently_reserved": 800,
    "your_estimated_cost": 500,
    "available": 200,
    "retry_after_seconds": seconds_until_midnight(),
}
```

### 6.4 Atomic check-and-increment vs check-then-increment

The naive approach has a race:

```python
# WRONG
current = await r.get(key)              # ← read
if int(current or 0) + estimated > limit:
    raise HTTPException(429)
await r.incrby(key, estimated)          # ← write (race here)
```

Two requests can both pass the check before either writes, both INCRBY past the limit. The implementation in section 4 uses **`INCRBY` first, check after, decrement on rejection** — atomic at the Redis level.

Even better: do it in Lua atomically with no rollback:

```lua
-- KEYS[1] = tpd key
-- ARGV[1] = estimated
-- ARGV[2] = limit
-- ARGV[3] = ttl_seconds
local cur = tonumber(redis.call("GET", KEYS[1]) or "0")
if cur + tonumber(ARGV[1]) > tonumber(ARGV[2]) then
  return {0, cur, tonumber(ARGV[2]) - cur}  -- denied, current, available
end
local new = redis.call("INCRBY", KEYS[1], tonumber(ARGV[1]))
if new == tonumber(ARGV[1]) then
  redis.call("EXPIRE", KEYS[1], tonumber(ARGV[3]))
end
return {1, new, tonumber(ARGV[2]) - new}
```

This avoids the brief over-count visible to other concurrent admissions during the rollback window.

### 6.5 Refund storms after a downstream outage

Provider goes down → all calls fail → all reservations refund. Suddenly your user counters look healthy and a flood of *new* requests is admitted. They all retry against the same broken provider. Rinse, repeat.

The fix is a **circuit breaker** that opens before admission completes — see [Part 6 — Advanced Patterns](06_advanced_patterns.md). With a global circuit breaker open, admission rejects immediately without reserving, so refunds aren't even a question.

### 6.6 Prompt caching (Anthropic, OpenAI)

If you're using prompt caching, billed tokens differ from input tokens. The response will include something like:

```python
response.usage = {
    "prompt_tokens": 5000,
    "cached_prompt_tokens": 4500,    # cheaper
    "completion_tokens": 200,
    "total_tokens": 5200,
}
```

You need to decide what counts against the user's quota:

- **Bill total_tokens**: simple, slightly unfair (cached tokens are nearly free).
- **Bill weighted total**: `cached × 0.1 + uncached × 1.0 + completion × 1.0`. Matches actual cost.

Pick one and document it. The reconciliation step uses whichever computed number you choose.

---

## 7. Summary

| Step | Counter | When | What |
|------|---------|------|------|
| Estimate | — | Before admission | Compute `prompt + max_output` (or heuristic) |
| Reserve | `user:{id}:tpd:{date}` | Once per logical request | `INCRBY estimated`, reject if over |
| Per attempt | `provider:{name}:rpm:{window}` | Each network call | `INCR` |
| Per attempt | `provider:{name}:tpm:{window}` | After each call returns usage | `INCRBY actual_or_partial` |
| Reconcile (success) | `user:{id}:tpd:{date}` | Once at end | `DECRBY (estimated - actual)` |
| Reconcile (failure) | `user:{id}:tpd:{date}` | Once at end | `DECRBY estimated` (full refund) |

### The mental model

> **Reserve before you call. Reconcile after. Never touch the user counter between attempts.**

Stick to that and your quota dashboards will match reality. Deviate and you'll spend Friday afternoons reconstructing what happened to a customer's "missing" tokens.

---

## Cost Observability — What to Dashboard

The Reserve → Retry → Reconcile pattern gives you the raw data for cost tracking. Turn it into operational visibility:

### Per-request attribution

Every call should emit a metric with **tenant**, **provider**, **model**, **input_tokens**, **output_tokens**, **cost_usd**, **success_bool**. In Prometheus:

```python
LLM_CALL_TOKENS = Histogram(
    "llm_call_tokens",
    "Tokens consumed per LLM call",
    labelnames=["tenant", "provider", "model", "kind"],   # kind = input|output
    buckets=[100, 500, 1000, 5000, 10000, 50000, 100000],
)

LLM_CALL_COST_USD = Counter(
    "llm_call_cost_usd_total",
    "Cumulative USD cost",
    labelnames=["tenant", "provider", "model"],
)


def record_call(tenant: str, provider: str, model: str, usage: Usage, cost: float):
    LLM_CALL_TOKENS.labels(tenant, provider, model, "input").observe(usage.input_tokens)
    LLM_CALL_TOKENS.labels(tenant, provider, model, "output").observe(usage.output_tokens)
    LLM_CALL_COST_USD.labels(tenant, provider, model).inc(cost)
```

With those two metrics you can graph cost per tenant, cost per feature (group by caller), cost per model — and catch the "one tenant is 90% of our spend" pattern before the invoice does.

### Burn-rate SLO

Treat your cost budget the way SRE treats error budget. If your monthly ceiling is `$X`, measure burn rate in dollars-per-hour and alert when you're on a trajectory to exceed the ceiling:

```
30d budget:            $10,000
Current month spend:   $6,200 (at day 18/30)
Budget remaining:      $3,800 for 12 days
Burn rate (24h avg):   $280/day
Projected to month-end: $6,200 + 12 × $280 = $9,560   OK
```

Multi-window burn-rate alerts work well: a 1-hour window that's 10x budget-pace fires fast for incidents; a 6-hour window that's 2x pace fires slower for sustained drift.

### Retry Budgets — The Other Cost

Retries multiply cost. A request with 3 retries costs 4x the base call. Without a budget, a pathological retry loop (retry everything on any error) silently triples your bill during an upstream outage.

**A retry budget caps the ratio of retries to original calls.** Google SRE Book's classic formulation: "retries shall be no more than 10% of original request volume." Implementation:

```python
# Pseudocode — sliding window in Redis
async def should_retry(endpoint: str) -> bool:
    originals = await r.get(f"calls:originals:{endpoint}:1m") or 1
    retries = await r.get(f"calls:retries:{endpoint}:1m") or 0
    return int(retries) < 0.10 * int(originals)  # 10% ceiling


async def call_with_budget(endpoint, fn):
    await r.incr(f"calls:originals:{endpoint}:1m")
    try:
        return await fn()
    except TransientError:
        if not await should_retry(endpoint):
            raise   # budget exhausted — fail fast, don't pile on
        await r.incr(f"calls:retries:{endpoint}:1m")
        return await fn()
```

When the upstream is healthy, retries are rare and budget is plentiful. When the upstream is degraded and retries are spiking, the budget clamps them — preventing a retry storm from amplifying the outage. This is the cost-side equivalent of a circuit breaker.

---

**Back to**: [README](README.md) | **Previous**: [Part 9 — Distributed Admission Control](09_distributed_admission_control.md) | **Next**: [Part 11 — Idempotency Keys](11_idempotency.md)
