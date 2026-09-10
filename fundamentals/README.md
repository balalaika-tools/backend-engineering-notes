# Fundamentals

> Core Python backend concepts. Start here before moving to infrastructure, background work, or architecture topics.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-D71F00.svg)](https://www.sqlalchemy.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-336791.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org)

---

## Contents

| Section | Description |
|---------|-------------|
| [core_concepts/](core_concepts/README.md) | Python contracts, iteration, decorators, exceptions, logging, configuration |
| [concurrency/](concurrency/README.md) | Threads, processes, async/await, the GIL, event loops, contextvars, production patterns |
| [httpx/](httpx/README.md) | HTTP client internals — pooling, timeouts, streaming |
| [fastapi/](fastapi/README.md) | Framework patterns, DI, Pydantic, auth, middleware, WebSockets, streaming, API design |
| [database/](database/README.md) | PostgreSQL, Python drivers, SQLAlchemy ORM, async patterns, connection pooling |
| [auth/](auth/README.md) | JWT, OAuth 2.0, AWS Cognito |

---

## Reading Order

### New to Python Backend

**Working result by entry 2**: run one FastAPI route and explain its request mapping.

1. **Do:** [FastAPI quick start](fastapi/README.md#quick-start-one-route-and-one-owned-http-client) — observe the exact `200` response.
2. **Understand:** [HTTP and parameter mapping](fastapi/01_http_and_parameter_mapping.md) — trace request data into a function signature.
3. **Compose:** [Dependency injection](fastapi/02_dependency_injection.md) and [Pydantic](fastapi/03_pydantic.md) — own resources and validate boundaries.

**First stop:** after step 3, you can build a route with validated input and application-owned
dependencies. Continue only when you need to choose an execution model, call another service, or
persist state.

4. **Choose execution:** [Concurrency decision guide](concurrency/00_decision_guide.md) — select async, threads, or processes from the blocking boundary.
5. **Call dependencies:** [HTTPX mental model](httpx/01_mental_model.md), [pooling](httpx/02_connection_pooling.md), and [timeouts](httpx/03_timeouts.md).
6. **Persist:** [Database foundations](database/01_databases_and_schemas.md), then [SQLAlchemy ORM](database/03_sqlalchemy_orm.md).

**Persistence stop:** after step 6, you can also map a constrained relational schema. Continue into
[Core Concepts](core_concepts/README.md) when the application needs reusable language, logging,
configuration, or shutdown mechanisms.

### Building a Production API

1. [core_concepts/configuration.md](core_concepts/configuration.md) — settings management
2. [fastapi/04_authentication.md](fastapi/04_authentication.md) — JWT, OAuth2
3. [fastapi/05_middleware.md](fastapi/05_middleware.md) — request ID, CORS, timing
4. [fastapi/07_error_handling.md](fastapi/07_error_handling.md) — consistent error shapes
5. [fastapi/10_api_design.md](fastapi/10_api_design.md) — resources, pagination, versioning
6. [core_concepts/structlog_guide.md](core_concepts/structlog_guide.md) — structured logging
7. [concurrency/async/03_contextvars.md](concurrency/async/03_contextvars.md) — request-scoped state

### Calling External APIs / LLMs

1. [httpx/](httpx/README.md) — understand the HTTP client
2. [fastapi/safe_and_scalable_api_calls/](fastapi/safe_and_scalable_api_calls/README.md) — production patterns
