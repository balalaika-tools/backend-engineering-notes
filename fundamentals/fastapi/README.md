# FastAPI Guides

> Production-ready patterns and best practices for FastAPI applications.

[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063.svg)](https://pydantic.dev)
[![Starlette](https://img.shields.io/badge/Starlette-0.40+-009688.svg)](https://www.starlette.io)

---

## Contents

### Core Guides

| Part | Topic | Description |
|------|-------|-------------|
| [01](01_http_and_parameter_mapping.md) | HTTP & Parameter Mapping | HTTP request structure, FastAPI's parameter resolution rules |
| [02](02_dependency_injection.md) | Dependency Injection | `Depends` mental model, patterns, testing |
| [03](03_pydantic.md) | Pydantic | Data validation, serialization, FastAPI integration |
| [04](04_authentication.md) | Authentication & Security | JWT, OAuth2, CORS, password hashing, security headers |
| [05](05_middleware.md) | Middleware | Request ID, timing, CORS, error handling, ordering |
| [06](06_websockets.md) | WebSockets | Connections, rooms, auth, heartbeat, scaling |
| [07](07_error_handling.md) | Error Responses | Exception hierarchy, global handlers, consistent shapes |
| [08](08_streaming.md) | Streaming | StreamingResponse, SSE, file downloads, backpressure |
| [09](09_background_tasks_and_routers.md) | BackgroundTasks & APIRouter | Fire-and-forget after response, app structure, OpenAPI customization |
| [10](10_api_design.md) | API Design Conventions | REST shape, methods, pagination, versioning, OpenAPI hygiene |

### External API Calls

- **[Safe and Scalable API Calls](safe_and_scalable_api_calls/README.md)** — Production guide for calling LLMs and external services

  | Part | Topic | Description |
  |------|-------|-------------|
  | [01](safe_and_scalable_api_calls/01_core_concepts.md) | Core Concepts | Mental model, the real concurrency limit |
  | [02](safe_and_scalable_api_calls/02_concurrency_and_timeouts.md) | Concurrency & Timeouts | Timeout layers, asyncio vs httpx |
  | [03](safe_and_scalable_api_calls/03_call_patterns.md) | Call Patterns | Gold standard pattern, retry logic |
  | [04](safe_and_scalable_api_calls/04_kubernetes.md) | Kubernetes | Multi-pod concerns, local vs global |
  | [05](safe_and_scalable_api_calls/05_production_architecture.md) | Production Architecture | Complete stack, execution order |
  | [06](safe_and_scalable_api_calls/06_advanced_patterns.md) | Advanced Patterns | Circuit breakers, bulkheads, hedging, priority queues |
  | [07](safe_and_scalable_api_calls/07_streaming_patterns.md) | Streaming Patterns | SSE, streaming timeouts |
  | [08](safe_and_scalable_api_calls/08_streaming_advanced.md) | Streaming Advanced | Multi-stream, aggregation |
  | [09](safe_and_scalable_api_calls/09_distributed_admission_control.md) | Distributed Admission Control | Redis-centric admission, atomic Lua, retries & quota accounting |
  | [10](safe_and_scalable_api_calls/10_llm_token_economics.md) | LLM Token Economics | Reserve → Retry → Reconcile, cost observability, retry budgets |
  | [11](safe_and_scalable_api_calls/11_idempotency.md) | Idempotency Keys | Safe POST retries, dedup state machine, Postgres + Redis backends |

---

## Prerequisites

**[HTTPX Guide](../httpx/README.md)** — connection pooling, timeouts, HTTP client internals (required before Safe API Calls)

---

## Quick Reference

### Minimal Safe External Call Pattern

```python
import asyncio
import httpx
from aiolimiter import AsyncLimiter

client = httpx.AsyncClient(
    limits=httpx.Limits(max_connections=50),
    timeout=httpx.Timeout(connect=5.0, read=30.0),
)

sem = asyncio.Semaphore(50)
rate = AsyncLimiter(60, 60)


async def call_api(payload: dict):
    async with asyncio.timeout(5):           # queue timeout
        async with rate:                      # throughput
            async with sem:                   # concurrency
                async with asyncio.timeout(30):  # call timeout
                    response = await client.post(url, json=payload)
                    return response.json()
```

### Architecture Layers

```
API Gateway → ASGI Server → FastAPI → HTTP Client → Vendor
     ↓             ↓           ↓           ↓
  Flood       Admission    Business    Transport
 Protection    Control      Logic      Timeouts
```

---

## Reading Path

1. **First**: [HTTPX Guide](../httpx/README.md) — understand the HTTP client
2. **Then**: [Safe API Calls](safe_and_scalable_api_calls/README.md) — apply to production
