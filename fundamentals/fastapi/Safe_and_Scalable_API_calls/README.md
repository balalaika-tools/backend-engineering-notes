# Safe and Scalable External API Calls

> **Purpose**: Production-grade patterns for calling LLMs, agents, and external APIs from FastAPI.

---

## Prerequisites

Before reading this guide, understand HTTPX internals:

**[HTTPX Guide](../../httpx/README.md)** — connection pooling, timeouts, and HTTP client behavior.

---

## Core Principle

> **Concurrency is a transport-layer reality.**
> **Anything that does not limit sockets is advisory.**

The only hard concurrency limit in an external API system is imposed by the **HTTP client** via the number of simultaneously open sockets.

---

## Guide Structure

| Part | Topic | Description |
|------|-------|-------------|
| [01](01_core_concepts.md) | Core Concepts | Mental model, the real concurrency limit, fundamental bound |
| [02](02_concurrency_and_timeouts.md) | Concurrency & Timeouts | Timeout layers, asyncio vs httpx, what each controls |
| [03](03_call_patterns.md) | Call Patterns | Gold standard pattern, retry logic, exception handling |
| [04](04_kubernetes.md) | Kubernetes | Multi-pod concerns, local vs global limits |
| [05](05_production_architecture.md) | Production Architecture | Complete stack, execution order, deployment |
| [06](06_advanced_patterns.md) | Advanced Patterns | Circuit breakers, priority queues, load shedding |
| [07](07_streaming_patterns.md) | Streaming Patterns | SSE, streaming timeouts, semaphore duration |
| [08](08_streaming_advanced.md) | Streaming Advanced | Multi-stream, aggregation, circuit breakers for streaming |

---

## The Non-Negotiable Layers

A safe production stack requires all of these:

1. **Client connection limits** — hard physical cap on sockets
2. **Client transport timeouts** — real request termination
3. **Semaphore** — logical concurrency gate
4. **Rate limiter** — vendor quota compliance
5. **Application timeout** — task cancellation & system safety
6. **Queue timeout** — fail fast under overload

Removing any layer introduces known failure modes.

---

## Quick Start

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


async def call_api(payload: dict):
    for attempt in range(3):
        try:
            async with asyncio.timeout(5):      # queue timeout
                async with rate:                 # throughput control
                    async with sem:              # concurrency gate
                        async with asyncio.timeout(30):  # call timeout
                            response = await client.post(url, json=payload)
                            response.raise_for_status()
                            return response.json()
        
        except httpx.TimeoutException:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
        
        except asyncio.TimeoutError:
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            raise
```

---

## Key Principles

1. **Client limits are the only hard limit** — everything else is advisory
2. **`asyncio.timeout` does NOT terminate sockets** — only client timeouts do
3. **Rate limiting ≠ concurrency control** — they serve different purposes
4. **Retries must be outside semaphore** — never sleep while holding resources
5. **Per-pod limits don't protect vendors** — use Redis for global limits
6. **Queue timeout is not retryable** — overload should fail fast

---

## Common Mistakes This Guide Prevents

❌ Client without connection limits (unbounded sockets)
❌ Relying only on `asyncio.timeout` for request termination
❌ Semaphore inside retry loop (holding resource during sleep)
❌ No queue timeout (unbounded queues)
❌ Per-pod rate limiters for vendor protection
❌ In-memory circuit breakers in multi-pod setups
❌ Catching only `asyncio.TimeoutError` (missing httpx exceptions)
❌ Retrying queue timeouts (amplifies overload)

---

## Reading Path

### For Beginners

1. Read [Core Concepts](01_core_concepts.md) — understand the mental model
2. Read [Concurrency & Timeouts](02_concurrency_and_timeouts.md) — understand timeout layers
3. Read [Call Patterns](03_call_patterns.md) — implement the gold standard pattern

### For Production Deployment

1. Review Parts 1-3
2. Read [Kubernetes](04_kubernetes.md) — multi-pod concerns
3. Read [Production Architecture](05_production_architecture.md) — complete stack

### For Advanced Systems

1. Review Parts 1-5
2. Read [Advanced Patterns](06_advanced_patterns.md) — circuit breakers, etc.

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
  ├─ Circuit breaker (Redis)
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
