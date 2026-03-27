# Part 1: Core Concepts

> **Principle**: The only hard concurrency limit is at the socket level. Everything else is advisory.

---

## 1. The Real Concurrency Limit

The **only hard concurrency limit** in an external API or LLM system is imposed by the **HTTP client** via the number of simultaneously open sockets.

- **Concurrency = number of active TCP connections**
- This is enforced at the transport layer (kernel resources)
- Python-level constructs do not control this directly

**If the client does not limit connections, concurrency is effectively unbounded**, regardless of semaphores or rate limiters.

---

## 2. The Four Independent Dimensions

You must manage **four orthogonal concerns**:

| Dimension | Question | Mechanism | Scope |
|-----------|----------|-----------|-------|
| **Concurrency** | How many calls in flight? | Client limits + Semaphore | Transport + Application |
| **Throughput** | How many calls per time? | Rate limiter | Application |
| **Latency** | How long to wait? | Timeouts | Transport + Application |
| **Failures** | What when wrong? | Retry logic | Application |

Each dimension requires its **own control mechanism**. They cannot be mixed or substituted.

---

## 3. Precise Definitions

### Concurrency (In-Flight Requests)

How many requests have:
- Started
- Not yet finished (success, failure, or timeout)

**The concurrency that matters**:

> **Concurrency at the downstream bottleneck** (the vendor, database, etc.)

This is limited by:
1. Client connection pool (hard limit)
2. Semaphore (soft limit)

---

### Throughput (Rate)

How many requests are **allowed to start** per unit of time.

```
60 requests / minute
```

This applies to:
- First attempts
- Retries (critically important)

**Control mechanism**: Rate limiter

---

### Vendor Response Latency

How long the vendor **actually takes** to respond.

Important facts:
- This is **variable** (has a distribution: p50, p95, p99)
- You **do not control it**
- You can only **measure it**

---

### Call Timeout

The **maximum latency you are willing to tolerate** for a single attempt.

This is a **client-side budget**, not a measurement.

> Call timeout is an *upper bound you choose*, not the vendor's average latency.

---

## 4. The Fundamental Bound

With a rate limiter and a call timeout:

```
max_concurrency ≈ rate × call_timeout
```

**Example**:

```
rate = 1 req/sec
call_timeout = 30 sec

max_concurrency ≈ 30 concurrent calls
```

This bound holds **even with retries**, provided:
- Retries pass through the same rate limiter
- Each attempt is capped by the same call timeout

**Critical insight**:

> Retries do **not** increase maximum concurrency.
> They only consume capacity *within* the same `rate × call_timeout` envelope.

---

## 5. Why Rate Limiting Does NOT Control Concurrency

Rate limiters:
- Control **when requests may start**
- Do **not** control how many are in-flight
- Provide statistical, not hard, concurrency bounds

With rate = R and timeout = T:

```
expected concurrency ≈ R × T
```

**But**: Latency variance, retries, and cancellation delays can exceed this bound.

Rate limiting is necessary for vendor quota compliance, but insufficient for concurrency control.

---

## 6. Client Connection Limits Are Mandatory

Every HTTP / LLM client **must** explicitly configure connection pool limits.

```python
httpx.Limits(
    max_connections=50,
    max_keepalive_connections=20,
)
```

These limits:
- Prevent excessive open sockets
- Protect file descriptors, TLS state, buffers
- Define the **physical** concurrency ceiling

**Rule**: All higher-level limits must be ≤ client limits.

Without client limits:
- Semaphores become advisory
- Memory usage is unbounded
- Socket exhaustion is possible

---

## 7. What a Semaphore Actually Does

Semaphores:
- Prevent new requests from **starting**
- Act **before** sockets are opened
- Provide a **logical concurrency gate**

They do **NOT**:
- Close sockets
- Enforce transport-level limits
- Guarantee resource cleanup

**Semaphores are advisory unless aligned with client limits.**

```python
sem = asyncio.Semaphore(50)

async with sem:
    # Gate: only 50 coroutines can be here
    # But HTTP client controls actual socket usage
    await client.get(...)
```

---

## 8. Correct Layering (Non-Negotiable)

A safe production stack requires all layers:

| Layer | Mechanism | Purpose |
|-------|-----------|---------|
| 1 | Client connection limits | Hard physical cap on sockets |
| 2 | Client transport timeouts | Real request termination |
| 3 | Semaphore | Logical concurrency gate |
| 4 | Rate limiter | Vendor quota compliance |
| 5 | Application timeout | Task cancellation |
| 6 | Queue timeout | Fail fast under overload |

**Removing any layer introduces known failure modes.**

---

## 9. When a Semaphore May Be Omitted

A semaphore may be removed **only if**:

- Client connection limits are strict
- Client timeouts are enforced
- Latency variance is low
- Retries are rare and bounded
- Temporary concurrency overshoot is acceptable
- Decision is based on production metrics

This is an **optimization**, not a baseline.

---

## 10. How to Choose `call_timeout`

### What It Represents

`call_timeout` is **NOT**:
- Average vendor latency
- Expected latency
- "How long it usually takes"

It **IS**:

> The maximum vendor latency you are willing to wait before giving up.

---

### Practical Rule

Choose `call_timeout` based on:

1. Acceptable user-facing latency
2. SLA or SLO targets
3. Worst-case vendor behavior you'll tolerate

| Approach | When to use |
|----------|-------------|
| Slightly above **p95** | Balanced reliability |
| Around **p99** | High reliability requirements |
| Strict SLA budget | Hard latency guarantees |

**Example**:

```
Vendor p95 latency ≈ 18s
Vendor p99 latency ≈ 25s

Chosen call_timeout = 30s
```

This means:
- You accept that some calls will timeout
- You cap how much latency contributes to concurrency
- Calls exceeding 30s are failed, not queued

---

## 11. Global, Shared Primitives

These must be created **once** at app startup, **not per-request**.

```python
import asyncio
from contextlib import asynccontextmanager
from aiolimiter import AsyncLimiter
import httpx
from fastapi import FastAPI

# GLOBAL primitives — created once
llm_sem = asyncio.Semaphore(50)
llm_rate = AsyncLimiter(60, 60)
client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=50,
            max_keepalive_connections=20,
        ),
        timeout=httpx.Timeout(
            connect=5.0,
            read=30.0,
            write=5.0,
            pool=5.0,
        )
    )
    yield
    await client.aclose()


app = FastAPI(lifespan=lifespan)
```

> ⚠️ **Critical**: If these are created per request, they do NOTHING.

---

## 12. Mental Model Summary

| Mechanism | What it limits | Scope |
|-----------|----------------|-------|
| Client `max_connections` | Physical sockets | Transport |
| Semaphore | Logical concurrency | Application |
| Rate limiter | Starts per time | Application |
| Call timeout | Max latency | Transport + Application |

**The Formula**:

```
expected_concurrency ≈ rate × call_timeout
actual_max_concurrency = min(client_limit, semaphore_size)
```

---

## Key Principles

1. **Client limits are the only hard limit** — everything else is advisory
2. **Rate limiting ≠ concurrency control** — they serve different purposes
3. **Semaphores are logical gates** — they don't close sockets
4. **Timeouts define budgets** — not expected behavior
5. **Primitives must be global** — per-request = useless

---

**Next**: [Part 2: Concurrency and Timeouts](02_concurrency_and_timeouts.md) — detailed timeout layers and their purposes.
