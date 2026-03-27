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
| [core_concepts/](core_concepts/README.md) | Python primitives — decorators, exceptions, logging, config, contextvars |
| [concurrency/](concurrency/README.md) | Threads, processes, async/await, the GIL, event loops |
| [httpx/](httpx/README.md) | HTTP client internals — pooling, timeouts, streaming |
| [fastapi/](fastapi/README.md) | Framework patterns, DI, Pydantic, auth, middleware, WebSockets, streaming |
| [database/](database/README.md) | PostgreSQL, Python drivers, SQLAlchemy ORM, async patterns, connection pooling |
| [auth/](auth/README.md) | JWT, OAuth 2.0, AWS Cognito |

---

## Reading Order

### New to Python Backend

1. [core_concepts/](core_concepts/README.md) — decorators, exceptions, logging, contextvars
2. [concurrency/](concurrency/README.md) — threads, processes, async
3. [httpx/](httpx/README.md) — HTTP client internals
4. [fastapi/ 01-03](fastapi/README.md) — parameters, DI, Pydantic
5. [database/](database/README.md) — SQL → drivers → ORM → async patterns

### Building a Production API

1. [core_concepts/configuration.md](core_concepts/configuration.md) — settings management
2. [fastapi/04_authentication.md](fastapi/04_authentication.md) — JWT, OAuth2
3. [fastapi/05_middleware.md](fastapi/05_middleware.md) — request ID, CORS, timing
4. [fastapi/07_error_handling.md](fastapi/07_error_handling.md) — consistent error shapes
5. [core_concepts/structlog_guide.md](core_concepts/structlog_guide.md) — structured logging
6. [core_concepts/contextvars.md](core_concepts/contextvars.md) — request-scoped state

### Calling External APIs / LLMs

1. [httpx/](httpx/README.md) — understand the HTTP client
2. [fastapi/Safe_and_Scalable_API_calls/](fastapi/Safe_and_Scalable_API_calls/README.md) — production patterns
