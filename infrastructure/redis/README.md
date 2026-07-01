# Redis

> Everything you need to use Redis effectively in Python backends — data structures, messaging patterns, caching strategies, and Python client integration.

[![Redis](https://img.shields.io/badge/Redis-7.x%20%7C%208.x-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)
[![redis-py](https://img.shields.io/badge/redis--py-5.x-DC382D.svg)](https://github.com/redis/redis-py)
[![asyncio](https://img.shields.io/badge/redis.asyncio-included-DC382D.svg)](https://redis-py.readthedocs.io/en/stable/asyncio.html)

Redis is an **in-memory data structure store** that serves data over TCP. It is not just a key-value cache — it provides rich data structures (strings, hashes, lists, sets, sorted sets, streams) with atomic operations, making it a versatile tool for caching, messaging, rate limiting, session storage, and real-time analytics.

> **Mental model:** Redis = data structures served over TCP. Pick the right structure for the job, and Redis gives you atomic operations on it at memory speed.

---

## Contents

| File | Topic | What you'll learn |
|------|-------|-------------------|
| [01_data_structures.md](01_data_structures.md) | Data Structures & Commands | Strings, hashes, lists, sets, sorted sets, streams — with Python examples, key expiration, and memory management |
| [02_pubsub_and_streams.md](02_pubsub_and_streams.md) | Pub/Sub & Streams | Fire-and-forget Pub/Sub, durable Streams with consumer groups, comparison with Kafka |
| [03_caching_patterns.md](03_caching_patterns.md) | Caching Patterns | Cache-aside, write-through, write-behind, TTL strategies, eviction policies, cache stampede |
| [04_python_clients.md](04_python_clients.md) | Python Redis Clients | redis-py sync and async, connection pooling, pipelines, transactions, FastAPI integration |
| [05_rate_limiting.md](05_rate_limiting.md) | Rate Limiting Algorithms | Fixed window, sliding log, sliding counter (Cloudflare-style), token bucket — with atomic Lua scripts |
| [06_ha_and_persistence.md](06_ha_and_persistence.md) | HA & Persistence | RDB/AOF, replication, Sentinel vs Cluster, failure modes, `maxmemory`/eviction, production checklist |

---

## Learning Path

**New to Redis** → Start with `01` (data structures) to understand what Redis offers beyond simple key-value.

**Building real-time features** → Read `02` for Pub/Sub and Streams patterns.

**Adding caching to your app** → Read `03` for caching patterns, then `04` for Python client setup.

**Integrating with FastAPI** → Read `04` for connection pooling, dependency injection, and async client patterns.

**Performance / production concerns** → Check eviction policies in `03`, connection pooling in `04`.

**Rate limiting / quota enforcement** → Read `05` for the four canonical algorithms. The application-layer architecture that consumes them lives in [Safe and Scalable API Calls / 09](../../fundamentals/fastapi/safe_and_scalable_api_calls/09_distributed_admission_control.md).

**Running Redis in production (HA, persistence, failover)** → Read `06` before committing to a deployment topology.

---

## Key Decisions at a Glance

| Question | Answer |
|----------|--------|
| Redis for caching or messaging? | Both — but understand the trade-offs of each pattern |
| Pub/Sub or Streams? | Pub/Sub for fire-and-forget. Streams when you need durability and consumer groups. |
| Sync or async redis-py? | Async for FastAPI. Sync for scripts and CLI tools. |
| Connection pooling? | Always. Never create a new connection per request. |
| Redis or Kafka? | Redis Streams for moderate throughput. Kafka for massive scale and strict ordering. |
| Eviction policy? | `allkeys-lru` for pure cache. `volatile-lru` if you mix cached and persistent data. |

---

## Prerequisites

- **Basic Python** — `async`/`await`, context managers, type hints
- **Networking basics** — TCP connections, client-server model, connection pooling
- [Concurrency & Async](../../fundamentals/concurrency/async/01_event_loop_and_tasks.md) — event loops before using async Redis clients
- [FastAPI Dependency Injection](../../fundamentals/fastapi/02_dependency_injection.md) — for the FastAPI integration patterns in `04`
