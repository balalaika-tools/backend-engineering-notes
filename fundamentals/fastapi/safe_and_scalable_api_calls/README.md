# Safe and Scalable External API Calls

> **Who this is for**: FastAPI engineers calling LLMs, agents, and external APIs under bounded
> capacity and partial failure.

> **Key insight**: Admit work before execution and keep local overload distinct from retryable
> dependency failure.

---

## First runnable baseline: classify capacity separately from dependency failure

This self-contained transport makes no network call. It runs one success, one local admission
failure, and one downstream read failure, then closes the client:

```python
import asyncio
import httpx


class AdmissionTimeout(Exception):
    pass


async def provider(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/slow":
        raise httpx.ReadTimeout("provider stalled", request=request)
    return httpx.Response(200, json={"result": "ok"})


async def call(client: httpx.AsyncClient, gate: asyncio.Semaphore, path: str):
    try:
        async with asyncio.timeout(0.01):
            await gate.acquire()
    except TimeoutError as exc:
        raise AdmissionTimeout("local capacity unavailable") from exc

    try:
        return await client.get(f"https://provider.test{path}")
    finally:
        gate.release()


async def main() -> None:
    gate = asyncio.Semaphore(1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        response = await call(client, gate, "/ok")
        print(response.status_code, response.json())

        await gate.acquire()  # hold local capacity to make admission fail
        try:
            await call(client, gate, "/ok")
        except AdmissionTimeout:
            print("admission_timeout")
        finally:
            gate.release()

        try:
            await call(client, gate, "/slow")
        except httpx.ReadTimeout:
            print("downstream_read_timeout")


asyncio.run(main())
# 200 {'result': 'ok'}
# admission_timeout
# downstream_read_timeout
```

The semaphore wait fails before execution and must not be retried locally; `ReadTimeout` means an
admitted provider attempt stalled and may be eligible for a bounded retry if the operation is
idempotent. If the program hangs instead of printing `admission_timeout`, the capacity wait is not
inside its own deadline.

**Stop here** for a small local integration whose calls are already bounded and non-retryable.
Continue when real load introduces queues, retries, multiple pods, or shared provider quotas.

---

## Prerequisites

Before reading this guide, understand HTTPX internals:

**[HTTPX Guide](../../httpx/README.md)** — connection pooling, timeouts, and HTTP client behavior.

---

## Core Principle

Concurrency exists at several boundaries. A semaphore can strictly cap admitted application work,
while the HTTP client's pool strictly caps simultaneous connections. Neither cap substitutes for
the other: queued coroutines still consume memory, and a socket cap alone does not enforce tenant or
provider policy.

---

## Contents

| Part | Topic | Description |
|------|-------|-------------|
| [01](01_core_concepts.md) | Core Concepts | Mental model, the real concurrency limit, fundamental bound |
| [02](02_concurrency_and_timeouts.md) | Concurrency & Timeouts | Timeout layers, asyncio vs httpx, what each controls |
| [03](03_call_patterns.md) | Call Patterns | Gold standard pattern, retry logic, exception handling |
| [04](04_kubernetes.md) | Kubernetes | Multi-pod concerns, local vs global limits |
| [05](05_production_architecture.md) | Production Architecture | Complete stack, execution order, deployment |
| [06](06_advanced_patterns.md) | Advanced Patterns | Circuit breakers, bulkheads, hedging, priority queues, load shedding |
| [07](07_streaming_patterns.md) | Streaming Patterns | SSE, streaming timeouts, semaphore duration |
| [08](08_streaming_advanced.md) | Streaming Advanced | Multi-stream, aggregation, circuit breakers for streaming |
| [09](09_distributed_admission_control.md) | Distributed Admission Control | Redis-centric admission, key taxonomy, atomic Lua, retries & quota accounting |
| [10](10_llm_token_economics.md) | LLM Token Economics | Reserve → Retry → Reconcile pattern for token quotas; estimation strategies; cost observability & retry budgets |
| [11](11_idempotency.md) | Idempotency Keys | Safe POST retries, dedup state machine, Postgres + Redis implementations |

---

## The Non-Negotiable Layers

A safe production stack requires all of these:

1. **Client connection limits** — hard physical cap on sockets
2. **Client transport timeouts** — phase/inactivity bounds for connect, pool, write, and read waits
3. **Semaphore** — logical concurrency gate
4. **Rate limiter** — vendor quota compliance
5. **Application timeout** — task cancellation & system safety
6. **Queue timeout** — fail fast under overload

Removing any layer introduces known failure modes.

---

## Composed production pattern

### Minimal Safe Pattern

```python
import asyncio
import httpx
from aiolimiter import AsyncLimiter

# Client with mandatory connection limits
client = httpx.AsyncClient(
    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
)

# Application-level controls
sem = asyncio.Semaphore(50)
rate = AsyncLimiter(60, 60)


class AdmissionTimeout(Exception):
    pass


class AttemptDeadlineExceeded(Exception):
    pass


async def call_api(payload: dict):
    for attempt in range(3):
        try:
            try:
                async with asyncio.timeout(5):  # local admission timeout
                    await rate.acquire()
                    await sem.acquire()
            except TimeoutError as exc:
                raise AdmissionTimeout("local capacity unavailable") from exc

            try:
                try:
                    async with asyncio.timeout(30):  # downstream attempt deadline
                        response = await client.post(
                            "https://vendor.example/api", json=payload
                        )
                except TimeoutError as exc:
                    raise AttemptDeadlineExceeded from exc
                response.raise_for_status()
                return response.json()
            finally:
                sem.release()

        except AdmissionTimeout:
            raise  # local overload is deliberately not retried
        except (httpx.TimeoutException, AttemptDeadlineExceeded):
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
```

---

## Key Principles

1. **Outer deadlines bound total wall time** — `asyncio.timeout()` cancels the waiting task
2. **HTTPX timeouts bound transport phases/inactivity** — they are not a total deadline
3. **Rate limiting ≠ concurrency control** — they serve different purposes
4. **Retries must be outside semaphore** — never sleep while holding resources
5. **Per-pod limits don't protect vendors** — use Redis for global limits
6. **Queue timeout is not retryable** — overload should fail fast

---

## Common Mistakes This Guide Prevents

❌ Client without connection limits (unbounded sockets)
❌ Using only one total deadline without phase limits or failure-specific diagnostics
❌ Semaphore inside retry loop (holding resource during sleep)
❌ No queue timeout (unbounded queues)
❌ Per-pod rate limiters for vendor protection
❌ One circuit-breaker state shared across unrelated dependency partitions or failure domains
❌ Catching only `asyncio.TimeoutError` (missing httpx exceptions)
❌ Retrying queue timeouts (amplifies overload)

---

## Learning paths

### For Beginners

**Working result by entry 2**: run the Quick Start above, then explain why admission failure and a
downstream attempt timeout take different retry paths.

1. **Do:** run the [first baseline](#first-runnable-baseline-classify-capacity-separately-from-dependency-failure) and observe all three outputs.
2. **Understand:** use its admission-versus-attempt contrast, then read [Call Patterns](03_call_patterns.md) for the canonical composed implementation.
3. **Deepen as needed:** [Core Concepts](01_core_concepts.md) and [Concurrency & Timeouts](02_concurrency_and_timeouts.md) own the full capacity and timeout references.
3. **Harden:** [Call Patterns](03_call_patterns.md) — add the canonical composition and failure mapping.

**Stop here if** one process owns the quota and only idempotent operations are retried. Continue to
distributed admission or idempotency when those conditions stop holding.

### For Production Deployment

1. Review Parts 1-3
2. Read [Kubernetes](04_kubernetes.md) — multi-pod concerns
3. Read [Production Architecture](05_production_architecture.md) — complete stack

### For Advanced Systems

1. Review Parts 1-5
2. Read [Advanced Patterns](06_advanced_patterns.md) — circuit breakers, bulkheads, hedging, priority queues
3. Read [Idempotency Keys](11_idempotency.md) — make the aggressive retries from Part 3 safe for non-GET endpoints (double-charge protection)

### For Distributed / Multi-Pod Systems

1. Review Part 4 (Kubernetes) and Part 5 (Production Architecture)
2. Read [Distributed Admission Control](09_distributed_admission_control.md) — Redis as the single source of truth, atomic Lua admission, the "Redis + if statements" pattern that replaces in-process limiters
3. For LLM workloads, also read [LLM Token Economics](10_llm_token_economics.md) — Reserve → Retry → Reconcile

> **Note**: Parts 1–3 describe the **transport layer** (sockets, timeouts, in-process semaphores). Part 9 describes the **admission layer** (cluster-wide Redis-backed checks). Real systems need both. If you only read parts 1–3, you'll have a single-pod story that breaks when you scale out.

---

## Architecture at a Glance

```
Internet
  ↓
API Gateway (flood protection)
  ↓
Kubernetes Service
  ↓
ASGI Server (admission control)
  ↓
FastAPI
  ├─ Load shedding (local)
  ├─ User rate limiter (Redis)
  ├─ Queue timeout
  ├─ Circuit breaker (scoped to the dependency failure domain)
  ├─ Global vendor limiter (Redis)
  ├─ Local rate limiter
  ├─ Semaphore
  └─ Call timeout
  ↓
HTTP Client (with timeouts)
  ↓
Vendor API
```

---

## Dependencies

```bash
# Required
pip install fastapi uvicorn httpx aiolimiter

# For production (Redis-backed features)
pip install redis limits

# For structured logging
pip install structlog
```

---

**Start with [Part 1: Core Concepts →](01_core_concepts.md)**
