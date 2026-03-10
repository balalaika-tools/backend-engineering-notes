# Python Backend Development Notes

A collection of practical notes and patterns for building backend applications with Python, focused on AI projects.

---

## Contents

### Core Concepts

Fundamental Python patterns every backend developer needs.

| Guide | Description |
|-------|-------------|
| [Decorators](core_concepts/decorators.md) | `@` syntax, `functools.wraps`, parameterized decorators |
| [Exceptions](core_concepts/exceptions.md) | Propagation, `raise` variants, `raise from`, production patterns |
| [Logging](core_concepts/logging.md) | `logging` module, per-module vs universal, rotation |
| [Structured Logging](core_concepts/structlog_guide.md) | structlog, processors, FastAPI integration, contextvars |
| [Configuration](core_concepts/configuration.md) | pydantic-settings, `.env` files, secrets, validation |

### Concurrency & Parallelism

Understanding threads, processes, async, and when to use each.

| Guide | Description |
|-------|-------------|
| [Threads vs Processes vs Async](concurrency/threads_vs_processes_vs_async.md) | GIL, when to use each approach |
| [Threads and Processes](concurrency/threads_and_processes.md) | `ThreadPoolExecutor`, `ProcessPoolExecutor`, `submit`, `map` |
| [Async Tutorial](concurrency/async_tutorial.md) | Event loop, coroutines vs tasks, `TaskGroup`, `run_in_executor` |

### HTTP Clients

| Guide | Description |
|-------|-------------|
| [HTTPX Guide](httpx/README.md) | Connection pooling, timeouts, HTTP/2, streaming |

  - [Mental Model](httpx/01_mental_model.md) — Request lifecycle, sockets, connection pools
  - [Connection Pooling](httpx/02_connection_pooling.md) — Pool limits and configuration
  - [Timeouts](httpx/03_timeouts.md) — Phase-based timeout configuration
  - [Advanced Features](httpx/04_advanced.md) — HTTP/2, streaming, error handling
  - [HTTPX vs aiohttp](httpx/05_httpx_vs_aiohttp.md) — When to choose which

### FastAPI

Production-ready patterns and best practices.

| Guide | Description |
|-------|-------------|
| [01 — HTTP & Parameter Mapping](fastapi/01_http_and_parameter_mapping.md) | HTTP request structure, FastAPI's parameter resolution rules |
| [02 — Dependency Injection](fastapi/02_dependency_injection.md) | `Depends` mental model, patterns, testing |
| [03 — Pydantic](fastapi/03_pydantic.md) | Data validation, serialization, FastAPI integration |
| [04 — Authentication & Security](fastapi/04_authentication.md) | JWT, OAuth2, CORS, password hashing, security headers |
| [05 — Middleware](fastapi/05_middleware.md) | Request ID, timing, CORS, error handling, middleware ordering |
| [06 — WebSockets](fastapi/06_websockets.md) | Connections, rooms, auth, heartbeat, scaling with Redis |
| [07 — Error Responses](fastapi/07_error_handling.md) | Exception hierarchy, global handlers, consistent error shapes |
| [08 — Streaming](fastapi/08_streaming.md) | StreamingResponse, SSE, file downloads, backpressure |

### Safe & Scalable API Calls

Production guide for calling LLMs and external services — [Full README](fastapi/Safe_and_Scalable_API_calls/README.md).

| Guide | Description |
|-------|-------------|
| [01 — Core Concepts](fastapi/Safe_and_Scalable_API_calls/01_core_concepts.md) | Mental model, the real concurrency limit |
| [02 — Concurrency & Timeouts](fastapi/Safe_and_Scalable_API_calls/02_concurrency_and_timeouts.md) | Timeout layers, asyncio vs httpx |
| [03 — Call Patterns](fastapi/Safe_and_Scalable_API_calls/03_call_patterns.md) | Gold standard pattern, retry logic |
| [04 — Kubernetes](fastapi/Safe_and_Scalable_API_calls/04_kubernetes.md) | Multi-pod concerns, local vs global |
| [05 — Production Architecture](fastapi/Safe_and_Scalable_API_calls/05_production_architecture.md) | Complete stack, execution order |
| [06 — Advanced Patterns](fastapi/Safe_and_Scalable_API_calls/06_advanced_patterns.md) | Circuit breakers, priority queues |
| [07 — Streaming Patterns](fastapi/Safe_and_Scalable_API_calls/07_streaming_patterns.md) | SSE, streaming timeouts |
| [08 — Streaming Advanced](fastapi/Safe_and_Scalable_API_calls/08_streaming_advanced.md) | Multi-stream, aggregation |

### Database

PostgreSQL-focused — from SQL fundamentals to production async patterns. [Full README](database/README.md).

| Guide | Description |
|-------|-------------|
| [01 — Databases & Schemas](database/01_databases_and_schemas.md) | Relational DB foundations, PostgreSQL vs SQLite, data types, constraints, indexes, ACID, SQL |
| [02 — Python Drivers](database/02_python_drivers.md) | psycopg3 (sync + async) and asyncpg — raw driver usage, COPY protocol, bulk insert |
| [03 — SQLAlchemy ORM](database/03_sqlalchemy_orm.md) | ORM concepts, SQLAlchemy 2.0 models, relationships, Alembic migrations |
| [04 — Async SQLAlchemy](database/04_async_sqlalchemy.md) | Async engine/session, FastAPI integration, CRUD, transactions, eager loading, common mistakes |
| [05 — Connection Pooling](database/05_connection_pooling.md) | Pool sizing, PgBouncer, monitoring, failure modes, timeouts |

### Auth

JWT and OAuth theory, plus AWS Cognito in practice. [Full README](auth/README.md).

| Guide | Description |
|-------|-------------|
| [JWT](auth/jwt.md) | JWT structure, JWKS, trust chain, validation algorithm, Python utilities |
| [OAuth 2.0](auth/oauth2.md) | Grant types, scopes, resource servers, M2M vs user-based patterns |
| [Cognito — Mental Model](auth/cognito/cognito.md) | User Pools vs Identity Pools, app clients, groups |
| [Cognito — User Pool](auth/cognito/user-pool.md) | Pool setup, auth flows, user management, Lambda triggers, boto3 |
| [Cognito — Tokens](auth/cognito/tokens.md) | IdToken vs AccessToken, Cognito claims, validation, revocation |
| [Cognito — OAuth in Practice](auth/cognito/oauth-jwt-guide.md) | M2M (client_credentials), user auth, FastAPI integration, testing |

### Testing

| Guide | Description |
|-------|-------------|
| [FastAPI Testing](testing/fastapi_testing.md) | pytest, AsyncClient, dependency overrides, fixtures, mocking |

### Deployment

| Guide | Description |
|-------|-------------|
| [Docker & Deployment](deployment/docker_and_deployment.md) | Multi-stage builds, Uvicorn, Gunicorn, health checks, graceful shutdown |

### Background Tasks

| Guide | Description |
|-------|-------------|
| [Background Tasks & Queues](background_tasks/background_tasks_and_queues.md) | Celery vs FastAPI BackgroundTasks vs AsyncIOScheduler |

---

## Reading Order

### New to Python Backend

1. [Core Concepts](core_concepts/README.md) — decorators, exceptions, logging
2. [Concurrency](concurrency/README.md) — threads, processes, async
3. [HTTPX](httpx/README.md) — HTTP client internals
4. [FastAPI 01-03](fastapi/README.md) — parameters, DI, Pydantic
5. [Database](database/README.md) — SQL foundations → drivers → ORM → async patterns
6. [Testing](testing/fastapi_testing.md) — pytest + FastAPI

### Building a Production API

1. [Configuration](core_concepts/configuration.md) — settings management
2. [Authentication](fastapi/04_authentication.md) — JWT, OAuth2
3. [Middleware](fastapi/05_middleware.md) — request ID, timing, CORS
4. [Error Handling](fastapi/07_error_handling.md) — consistent error responses
5. [Structured Logging](core_concepts/structlog_guide.md) — structlog
6. [Docker](deployment/docker_and_deployment.md) — containerization

### Calling External APIs / LLMs

1. [HTTPX Guide](httpx/README.md) — understand the HTTP client
2. [Safe API Calls](fastapi/Safe_and_Scalable_API_calls/README.md) — production patterns

---

## Focus Areas

This repository covers patterns and practices for:
- Backend API development with FastAPI
- AI/ML project backends (LLM API calls, streaming)
- Production-ready code patterns
- Concurrency and parallelism
- Database access (async SQLAlchemy)
- Authentication and security
- Testing and deployment

---

*Last updated: March 2026*
