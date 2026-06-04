# Python Backend Development Notes

> Practical patterns for building production-grade Python backends — FastAPI, async SQLAlchemy, Redis, Dramatiq, and more. Focused on AI and API-heavy projects.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-D71F00.svg)](https://www.sqlalchemy.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-336791.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7.x-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063.svg)](https://pydantic.dev)
[![Dramatiq](https://img.shields.io/badge/Dramatiq-1.17+-7B4EA6.svg)](https://dramatiq.io)

---

## Structure

```
python-backend-notes/
│
│ ── FUNDAMENTALS ────────────────────────────────────────
├── fundamentals/
│   ├── core_concepts/     Python primitives — decorators, exceptions, logging, config, contextvars
│   ├── concurrency/       Threads, processes, async/await, event loops
│   ├── httpx/             HTTP client internals
│   ├── fastapi/           Framework patterns + Safe & Scalable API calls
│   ├── database/          PostgreSQL, SQLAlchemy, Alembic, async patterns
│   └── auth/              JWT, OAuth 2.0, AWS Cognito
│
│ ── INFRASTRUCTURE ──────────────────────────────────────
├── infrastructure/
│   └── redis/             Data structures, caching, pub/sub, Python clients
│
│ ── BACKGROUND WORK ─────────────────────────────────────
├── background_work/       Dramatiq, APScheduler, Airflow, FastAPI BackgroundTasks
│
│ ── ADVANCED ARCHITECTURE ───────────────────────────────
├── architecture/
│   └── long_running_tasks/  Orchestration, worker patterns, client delivery, infra
│
│ ── OPERATIONS ──────────────────────────────────────────
└── operations/
    ├── testing/           pytest, AsyncClient, dependency overrides, fixtures
    └── deployment/        Docker, Uvicorn, Gunicorn, health checks
```

---

## Contents

### Fundamentals — [full index](fundamentals/README.md)

#### Core Concepts

[![Python](https://img.shields.io/badge/Python-stdlib-3776AB.svg?logo=python&logoColor=white)](fundamentals/core_concepts/)
[![structlog](https://img.shields.io/badge/structlog-latest-4B8BBE.svg)](https://www.structlog.org)
[![pydantic-settings](https://img.shields.io/badge/pydantic--settings-2.x-E92063.svg)](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

| Guide | Description |
|-------|-------------|
| [Typing](fundamentals/core_concepts/typing.md) | `Optional` (preferred), `Literal`, `TypedDict`, `TypeVar`, `Protocol` |
| [Context Managers](fundamentals/core_concepts/context_managers.md) | `with` / `async with`, `@contextmanager`, `ExitStack`, pitfalls |
| [Decorators](fundamentals/core_concepts/decorators.md) | `@` syntax, `functools.wraps`, parameterized decorators |
| [Exceptions](fundamentals/core_concepts/exceptions.md) | Propagation, `raise` variants, `raise from`, production patterns |
| [Logging](fundamentals/core_concepts/logging/README.md) | Logger hierarchy, `propagate`, handler/formatter pipeline, per-module vs universal |
| [Structured Logging](fundamentals/core_concepts/structlog_guide.md) | structlog, processors, FastAPI integration, contextvars |
| [Configuration](fundamentals/core_concepts/configuration.md) | pydantic-settings, `.env` files, secrets, validation |
| [ContextVars](fundamentals/core_concepts/contextvars.md) | Request-scoped state, async-safe thread-locals, propagation patterns |

#### Concurrency & Parallelism

[![asyncio](https://img.shields.io/badge/asyncio-stdlib-3776AB.svg?logo=python&logoColor=white)](fundamentals/concurrency/)

| Guide | Description |
|-------|-------------|
| [Threads vs Processes vs Async](fundamentals/concurrency/threads_vs_processes_vs_async.md) | GIL, free-threaded Python (3.14), when to use threads vs processes vs async |
| [Threads and Processes](fundamentals/concurrency/threads_and_processes.md) | `ThreadPoolExecutor`, `ProcessPoolExecutor`, `InterpreterPoolExecutor`, `submit`, `map`, `as_completed` |
| [Async Tutorial](fundamentals/concurrency/async_tutorial.md) | Event loop, coroutines vs tasks, `TaskGroup`, `run_in_executor` |
| [Production Patterns](fundamentals/concurrency/production_patterns.md) | Semaphores, queues, timeouts, cancellation, `contextvars`, graceful shutdown |

#### HTTP Clients

[![HTTPX](https://img.shields.io/badge/HTTPX-0.27+-009688.svg)](https://www.python-httpx.org)

| Guide | Description |
|-------|-------------|
| [Mental Model](fundamentals/httpx/01_mental_model.md) | Request lifecycle, sockets, connection pools |
| [Connection Pooling](fundamentals/httpx/02_connection_pooling.md) | Pool limits and configuration |
| [Timeouts](fundamentals/httpx/03_timeouts.md) | Phase-based timeout configuration |
| [Advanced Features](fundamentals/httpx/04_advanced.md) | HTTP/2, streaming, error handling |
| [HTTPX vs aiohttp](fundamentals/httpx/05_httpx_vs_aiohttp.md) | When to choose which |

#### FastAPI

[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063.svg)](https://pydantic.dev)

| Guide | Description |
|-------|-------------|
| [01 — HTTP & Parameter Mapping](fundamentals/fastapi/01_http_and_parameter_mapping.md) | HTTP request structure, parameter resolution |
| [02 — Dependency Injection](fundamentals/fastapi/02_dependency_injection.md) | `Depends` mental model, patterns, testing |
| [03 — Pydantic](fundamentals/fastapi/03_pydantic.md) | Data validation, serialization, FastAPI integration |
| [04 — Authentication & Security](fundamentals/fastapi/04_authentication.md) | JWT, OAuth2, CORS, password hashing |
| [05 — Middleware](fundamentals/fastapi/05_middleware.md) | Request ID, timing, CORS, error handling, ordering |
| [06 — WebSockets](fundamentals/fastapi/06_websockets.md) | Connections, rooms, auth, heartbeat, scaling with Redis |
| [07 — Error Responses](fundamentals/fastapi/07_error_handling.md) | Exception hierarchy, global handlers, consistent error shapes |
| [08 — Streaming](fundamentals/fastapi/08_streaming.md) | StreamingResponse, SSE, file downloads, backpressure |
| [09 — BackgroundTasks & APIRouter](fundamentals/fastapi/09_background_tasks_and_routers.md) | Fire-and-forget, app structure, OpenAPI customization |

#### Safe & Scalable API Calls — [full README](fundamentals/fastapi/Safe_and_Scalable_API_calls/README.md)

| Guide | Description |
|-------|-------------|
| [01 — Core Concepts](fundamentals/fastapi/Safe_and_Scalable_API_calls/01_core_concepts.md) | Mental model, the real concurrency limit |
| [02 — Concurrency & Timeouts](fundamentals/fastapi/Safe_and_Scalable_API_calls/02_concurrency_and_timeouts.md) | Timeout layers, asyncio vs httpx |
| [03 — Call Patterns](fundamentals/fastapi/Safe_and_Scalable_API_calls/03_call_patterns.md) | Gold standard pattern, retry logic |
| [04 — Kubernetes](fundamentals/fastapi/Safe_and_Scalable_API_calls/04_kubernetes.md) | Multi-pod concerns, local vs global |
| [05 — Production Architecture](fundamentals/fastapi/Safe_and_Scalable_API_calls/05_production_architecture.md) | Complete stack, execution order |
| [06 — Advanced Patterns](fundamentals/fastapi/Safe_and_Scalable_API_calls/06_advanced_patterns.md) | Circuit breakers, priority queues |
| [07 — Streaming Patterns](fundamentals/fastapi/Safe_and_Scalable_API_calls/07_streaming_patterns.md) | SSE, streaming timeouts |
| [08 — Streaming Advanced](fundamentals/fastapi/Safe_and_Scalable_API_calls/08_streaming_advanced.md) | Multi-stream, aggregation |
| [09 — Distributed Admission Control](fundamentals/fastapi/Safe_and_Scalable_API_calls/09_distributed_admission_control.md) | Cross-pod concurrency limits, Redis-backed atomic limiters |
| [10 — LLM Token Economics](fundamentals/fastapi/Safe_and_Scalable_API_calls/10_llm_token_economics.md) | Per-tenant budgets, token accounting, cost observability, retry budgets |
| [11 — Idempotency Keys](fundamentals/fastapi/Safe_and_Scalable_API_calls/11_idempotency.md) | Safe POST retries, dedup state machine, Postgres + Redis implementations |

#### Database — [full README](fundamentals/database/README.md)

[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-336791.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-D71F00.svg)](https://www.sqlalchemy.org)
[![asyncpg](https://img.shields.io/badge/asyncpg-0.29+-2E6FA3.svg)](https://github.com/MagicStack/asyncpg)
[![Alembic](https://img.shields.io/badge/Alembic-1.x-6BA81E.svg)](https://alembic.sqlalchemy.org)

| Guide | Description |
|-------|-------------|
| [01 — Databases & Schemas](fundamentals/database/01_databases_and_schemas.md) | Relational DB foundations, PostgreSQL, ACID, SQL |
| [02 — Python Drivers](fundamentals/database/02_python_drivers.md) | psycopg3, asyncpg — raw driver usage, COPY, bulk insert |
| [03 — SQLAlchemy ORM](fundamentals/database/03_sqlalchemy_orm.md) | ORM concepts, SQLAlchemy 2.0, relationships, basic Alembic setup |
| [04 — Async SQLAlchemy](fundamentals/database/04_async_sqlalchemy.md) | Async engine/session, FastAPI integration, CRUD, transactions |
| [05 — Connection Pooling](fundamentals/database/05_connection_pooling.md) | Pool sizing, PgBouncer, monitoring, failure modes |
| [06 — Alembic Deep-Dive](fundamentals/database/06_alembic.md) | Data migrations, enums, branching, CI/CD, production safety, lock-free patterns |

#### Auth — [full README](fundamentals/auth/README.md)

[![JWT](https://img.shields.io/badge/JWT-RFC7519-000000.svg?logo=jsonwebtokens&logoColor=white)](https://jwt.io)
[![OAuth2](https://img.shields.io/badge/OAuth2-RFC6749-EB5424.svg)](https://oauth.net/2/)
[![AWS Cognito](https://img.shields.io/badge/AWS_Cognito-latest-FF9900.svg?logo=amazonaws&logoColor=white)](https://aws.amazon.com/cognito/)

| Guide | Description |
|-------|-------------|
| [JWT](fundamentals/auth/jwt.md) | JWT structure, JWKS, trust chain, validation algorithm |
| [OAuth 2.0](fundamentals/auth/oauth2.md) | Grant types, scopes, resource servers, M2M vs user-based |
| [Cognito — Mental Model](fundamentals/auth/cognito/cognito.md) | User Pools vs Identity Pools, app clients, groups |
| [Cognito — User Pool](fundamentals/auth/cognito/user-pool.md) | Pool setup, auth flows, user management, Lambda triggers |
| [Cognito — Tokens](fundamentals/auth/cognito/tokens.md) | IdToken vs AccessToken, Cognito claims, validation |
| [Cognito — OAuth in Practice](fundamentals/auth/cognito/oauth-jwt-guide.md) | M2M, user auth, FastAPI integration, testing |

---

### Infrastructure — [full index](infrastructure/README.md)

#### Redis — [full README](infrastructure/redis/README.md)

[![Redis](https://img.shields.io/badge/Redis-7.x-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)
[![redis-py](https://img.shields.io/badge/redis--py-5.x-DC382D.svg)](https://github.com/redis/redis-py)

| Guide | Description |
|-------|-------------|
| [01 — Data Structures](infrastructure/redis/01_data_structures.md) | Strings, hashes, lists, sets, sorted sets, streams, expiration |
| [02 — Pub/Sub & Streams](infrastructure/redis/02_pubsub_and_streams.md) | Fire-and-forget pub/sub, durable streams, consumer groups |
| [03 — Caching Patterns](infrastructure/redis/03_caching_patterns.md) | Cache-aside, write-through, TTL, eviction, stampede prevention |
| [04 — Python Clients](infrastructure/redis/04_python_clients.md) | redis-py sync/async, connection pooling, pipelines, FastAPI integration |
| [05 — Rate Limiting](infrastructure/redis/05_rate_limiting.md) | Token bucket, sliding/fixed window, atomic Lua scripts |
| [06 — HA & Persistence](infrastructure/redis/06_ha_and_persistence.md) | RDB/AOF, replication, Sentinel vs Cluster, failure modes, `maxmemory` |

---

### Background Work — [full README](background_work/README.md)

[![Dramatiq](https://img.shields.io/badge/Dramatiq-1.17+-7B4EA6.svg)](https://dramatiq.io)
[![APScheduler](https://img.shields.io/badge/APScheduler-3.x-4B8BBE.svg)](https://apscheduler.readthedocs.io)
[![Airflow](https://img.shields.io/badge/Airflow-2.10+-017CEE.svg?logo=apacheairflow&logoColor=white)](https://airflow.apache.org)

| Guide | Description |
|-------|-------------|
| [01 — Overview](background_work/01_overview.md) | Dramatiq vs BackgroundTasks vs APScheduler vs Airflow — when to use what |
| [02 — Dramatiq](background_work/02_dramatiq.md) | Actors, brokers, retries, rate limits, pipelines, monitoring |
| [03 — Dramatiq + FastAPI](background_work/03_dramatiq_fastapi.md) | Integration patterns, project structure, testing, Docker Compose |
| [04 — APScheduler](background_work/04_apscheduler.md) | Scheduled jobs, triggers, job stores, the multi-instance problem |
| [05 — Airflow](background_work/05_airflow.md) | DAGs, TaskFlow API, operators, sensors, dynamic mapping, production patterns |

---

### Advanced Architecture — [full README](architecture/README.md)

| Guide | Description |
|-------|-------------|
| [Long-Running Tasks](architecture/long_running_tasks/README.md) | Orchestration, worker patterns, client delivery, infrastructure |

---

### Operations — [full index](operations/README.md)

[![pytest](https://img.shields.io/badge/pytest-8.x-0A9EDC.svg?logo=pytest&logoColor=white)](https://pytest.org)
[![Docker](https://img.shields.io/badge/Docker-latest-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com)

| Guide | Description |
|-------|-------------|
| [FastAPI Testing](operations/testing/README.md) | 12-part guide — pytest, unit testing, endpoint testing, dependency overrides, fixtures, DB & mocking, coverage & CI |
| [Docker & Deployment](operations/deployment/docker_and_deployment.md) | Multi-stage builds, Uvicorn, Gunicorn, health checks, graceful shutdown |

---

## Reading Order

> [!TIP]
> Not sure where to start? Pick the path that matches your goal.

### New to Python Backend

1. [Core Concepts](fundamentals/core_concepts/README.md) — decorators, exceptions, logging, contextvars
2. [Concurrency](fundamentals/concurrency/README.md) — threads, processes, async
3. [HTTPX](fundamentals/httpx/README.md) — HTTP client internals
4. [FastAPI 01-03](fundamentals/fastapi/README.md) — parameters, DI, Pydantic
5. [Database](fundamentals/database/README.md) — SQL foundations → drivers → ORM → async patterns
6. [Testing](operations/testing/README.md) — pytest + FastAPI, unit → endpoint → coverage

### Building a Production API

1. [Configuration](fundamentals/core_concepts/configuration.md) — settings management
2. [Authentication](fundamentals/fastapi/04_authentication.md) — JWT, OAuth2
3. [Middleware](fundamentals/fastapi/05_middleware.md) — request ID, timing, CORS
4. [Error Handling](fundamentals/fastapi/07_error_handling.md) — consistent error responses
5. [Structured Logging](fundamentals/core_concepts/structlog_guide.md) — structlog
6. [ContextVars](fundamentals/core_concepts/contextvars.md) — request-scoped state propagation
7. [Docker](operations/deployment/docker_and_deployment.md) — containerization

### Calling External APIs / LLMs

1. [HTTPX Guide](fundamentals/httpx/README.md) — understand the HTTP client
2. [Safe API Calls](fundamentals/fastapi/Safe_and_Scalable_API_calls/README.md) — production patterns

### Background Work & Architecture

1. [Redis](infrastructure/redis/README.md) — data structures, caching, pub/sub
2. [Background Work](background_work/README.md) — Dramatiq, APScheduler, BackgroundTasks
3. [Long-Running Tasks](architecture/long_running_tasks/README.md) — orchestration, workers, delivery patterns

---

*Last updated: June 2026*
