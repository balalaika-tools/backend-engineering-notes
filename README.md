# Python Backend Development Notes

> Practical, production-oriented guides for designing, building, and operating Python backend systems — from APIs and data access to messaging, background work, architecture, testing, and deployment.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-D71F00.svg)](https://www.sqlalchemy.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-336791.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7.x-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)

---

## Start here

| If you want to… | Start with | Working outcome |
|---|---|---|
| Build your first Python API | [FastAPI quick start](fundamentals/fastapi/README.md#quick-start-one-route-and-one-owned-http-client) | Run a route with validated input and an owned HTTP client |
| Call external APIs or LLMs safely | [HTTPX](fundamentals/httpx/README.md) | Build a reusable client with bounded connections and timeouts |
| Move work outside the request | [Minimal Durable Task](background_work/foundations/03_minimal_durable_task.md) | Submit, claim, retry, and inspect a recoverable job |
| Separate business logic from transports | [Hexagonal Architecture](architecture/hexagonal_architecture/README.md) | Run one application action through API and worker adapters |

---

## Contents

### Foundations — [full index](fundamentals/README.md)

| Area | Covers | Start here |
|---|---|---|
| [Core Python](fundamentals/core_concepts/README.md) | Typing, iteration, data models, resource lifetimes, exceptions, logging, configuration, signals | [Type-checking workflow](fundamentals/core_concepts/typing_workflow.md) |
| [Concurrency](fundamentals/concurrency/README.md) | Asyncio, threads, processes, shared-state safety, and runtime choices | [Decision guide](fundamentals/concurrency/00_decision_guide.md) |
| [HTTP clients](fundamentals/httpx/README.md) | HTTPX request lifecycle, pooling, timeouts, streaming, and client selection | [Mental model](fundamentals/httpx/01_mental_model.md) |
| [FastAPI](fundamentals/fastapi/README.md) | HTTP mapping, dependency injection, Pydantic, authentication, middleware, streaming, and API security | [Quick start](fundamentals/fastapi/README.md#quick-start-one-route-and-one-owned-http-client) |
| [Databases](fundamentals/database/README.md) | PostgreSQL, Python drivers, SQLAlchemy, async sessions, pooling, Alembic, and SQLModel | [Databases and schemas](fundamentals/database/01_databases_and_schemas.md) |
| [Authentication](fundamentals/auth/README.md) | JWT, OAuth 2.0, AWS Cognito, token validation, and authorization boundaries | [JWT](fundamentals/auth/jwt.md) |

### Interfaces and infrastructure — [API index](apis/README.md) · [Infrastructure index](infrastructure/README.md)

| Area | Covers | Start here |
|---|---|---|
| [API Communication](apis/README.md) | REST, SOAP, GraphQL, gRPC, WebSockets, webhooks, contracts, and evolution | [API fundamentals](apis/01_api_fundamentals.md) |
| [Redis](infrastructure/redis/README.md) | Data structures, caching, streams, rate limiting, persistence, and high availability | [Data structures](infrastructure/redis/01_data_structures.md) |
| [Apache Kafka](infrastructure/kafka/README.md) | Retained logs, producers, consumers, schemas, reliability, operations, and ecosystem choices | [First event](infrastructure/kafka/fundamentals/README.md) |
| [NATS JetStream](infrastructure/nats_jetstream/README.md) | Durable subjects, pull consumers, Python workers, reliable effects, recovery, and multitenancy | [First durable round trip](infrastructure/nats_jetstream/01_first_durable_round_trip.md) |
| [Observability](infrastructure/observability/README.md) | OpenTelemetry, Python instrumentation, metrics export, collectors, and Prometheus | [Overview](infrastructure/observability/README.md) |

### Systems and production — [Architecture index](architecture/README.md) · [Operations index](operations/README.md)

| Area | Covers | Start here |
|---|---|---|
| [Background Work](background_work/README.md) | Durable tasks, queues, scheduling, stateful workflows, reliability, operations, and framework selection | [Minimal durable task](background_work/foundations/03_minimal_durable_task.md) |
| [Hexagonal Architecture](architecture/hexagonal_architecture/README.md) | Ports, adapters, composition, APIs, workers, GenAI, testing, and migration | [Why hexagonal architecture](architecture/hexagonal_architecture/01_why_hexagonal_architecture.md) |
| [Long-Running Tasks](architecture/long_running_tasks/README.md) | Orchestration, client delivery, worker patterns, infrastructure, sagas, and outbox | [Overview](architecture/long_running_tasks/README.md) |
| [Testing](operations/testing/README.md) | pytest, endpoint tests, dependency overrides, fixtures, databases, mocking, CI, and LLM tests | [Testing setup](operations/testing/02_setup.md) |
| [Deployment](operations/deployment/README.md) | Docker, Uvicorn, Gunicorn, health checks, graceful shutdown, and production rollout | [Docker and deployment](operations/deployment/docker_and_deployment.md) |

Each area index contains its complete ordered catalog of guides.

---

## Learning paths

> [!TIP]
> Not sure where to start? Pick the path that matches your goal.

### New to Python Backend

**For**: Python developers building their first backend service.

**Working result by entry 2**: run the FastAPI README's self-contained service and explain how
FastAPI maps its request into the route function.

1. **Do:** [FastAPI quick start](fundamentals/fastapi/README.md#quick-start-one-route-and-one-owned-http-client) — run one route and observe `200 {'provider': 'ok'}`.
2. **Understand:** [HTTP requests and parameter mapping](fundamentals/fastapi/01_http_and_parameter_mapping.md) — predict which request field becomes each function argument.
3. **Understand:** [Dependency injection](fundamentals/fastapi/02_dependency_injection.md) and [Pydantic](fundamentals/fastapi/03_pydantic.md) — add resource lifetimes and validated input/output boundaries.
4. **Harden:** [Concurrency decision guide](fundamentals/concurrency/00_decision_guide.md), then the [HTTPX path](fundamentals/httpx/README.md) — choose the blocking model and bound outbound calls.
5. **Persist:** [Databases and schemas](fundamentals/database/01_databases_and_schemas.md), then [SQLAlchemy ORM](fundamentals/database/03_sqlalchemy_orm.md) — create a constrained schema and map it without losing transaction boundaries.
6. **Verify:** [Testing setup](operations/testing/02_setup.md), [unit tests](operations/testing/03_unit_testing.md), and [endpoint tests](operations/testing/04_endpoint_testing.md) — produce a passing unit and in-process HTTP suite.

**Stop here if** you can serve, validate, persist, and test one bounded API. Continue into
[Core Concepts](fundamentals/core_concepts/README.md) when you need reusable typing, lifetime,
logging, configuration, or shutdown mechanisms.

### Building a Production API

**For**: engineers hardening an existing API rather than learning the first route.

**Working result by entry 2**: a resource/HTTP contract with an explicit production-hardening path.

1. [API Fundamentals and Selection](apis/README.md) — contracts and interaction choices
2. [RESTful APIs](apis/restful/README.md) — HTTP semantics, resources, reliability, security, and operations
3. [Configuration](fundamentals/core_concepts/configuration.md) — settings management
4. [Authentication](fundamentals/fastapi/04_authentication.md) — JWT, OAuth2
5. [Middleware](fundamentals/fastapi/05_middleware.md) — request ID, timing, CORS
6. [Error Handling](fundamentals/fastapi/07_error_handling.md) — consistent error responses
7. [API Design](fundamentals/fastapi/10_api_design.md) — FastAPI resource implementation
8. [Structured Logging](fundamentals/core_concepts/structlog_guide.md) — structlog
9. [Docker](operations/deployment/docker_and_deployment.md) — containerization

**Stop here if** the deployed API has explicit authorization, error, observability, and shutdown
contracts. Continue into the specialist REST chapters only when collections, caching, compatibility,
or a specific failure mode requires them.

### Calling External APIs / LLMs

**For**: services that call a provider under partial failure and shared quotas.

**Working result by entry 2**: choose the interaction boundary and run a reusable bounded HTTP client.

1. [API Styles and Selection](apis/02_api_styles_and_selection.md) — understand the integration boundary
2. [HTTPX Guide](fundamentals/httpx/README.md) — understand the HTTP client
3. [Safe API Calls](fundamentals/fastapi/safe_and_scalable_api_calls/README.md) — production patterns
4. [Webhooks](apis/webhooks/README.md) — durable callbacks when a provider pushes events
5. [Testing LLM Code](operations/testing/13_testing_llm_code.md) — prompt builders, adapters, schemas, evals

**Stop here if** the provider call is bounded, retry-safe, and covered by deterministic adapter tests.
Continue to webhooks only when the provider pushes durable events back to you.

### Background Work & Architecture

**For**: services whose work must outlive the request or process that accepted it.

**Working result by entry 2**: submit, claim, retry, and look up one durable task, then explain which
state belongs to the application rather than the queue.

1. **Do:** [Minimal Durable Task](background_work/foundations/03_minimal_durable_task.md) — build the smallest recoverable job and status endpoint.
2. **Understand:** [Background Work Overview](background_work/foundations/01_overview.md) — separate business, delivery, execution, and scheduling state around that result.
3. **Escalate deliberately:** [Task or Workflow?](background_work/foundations/02_task_or_workflow.md) — continue only when one job no longer captures the business lifecycle.
4. **Decide:** [System Selection Guide](background_work/frameworks/00_system_selection.md) — validate the smallest runtime that meets the recovery contract.

**Stop here if** one durable task and a result endpoint meet the product need. Continue to the [full Background Work course](background_work/README.md) for stateful workflows, reliability protocols, fan-out, and production operations; use [Long-Running Tasks](architecture/long_running_tasks/README.md) for client delivery, callbacks, and infrastructure-specific patterns.
