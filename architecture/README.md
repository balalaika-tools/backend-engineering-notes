# Advanced Architecture Patterns

> System-level design patterns that compose multiple foundational concepts into production architectures.

[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Redis](https://img.shields.io/badge/Redis-pub%2Fsub%20%2F%20streams-DC382D.svg?logo=redis&logoColor=white)](https://redis.io)
[![Dramatiq](https://img.shields.io/badge/Dramatiq-workers-7B4EA6.svg)](https://dramatiq.io)

These guides assume familiarity with the fundamentals. They show how the pieces compose into production systems.

---

## Contents

| Topic | Description |
|-------|-------------|
| [Hexagonal Architecture](hexagonal_architecture/README.md) | A 13-part path from one runnable vertical slice through ports, transactions, runtime composition, GenAI, testing, migration, and a service/shared-library case study |
| [Long-Running Tasks](long_running_tasks/README.md) | Handling requests that take seconds to hours: orchestration, worker patterns, client delivery, infrastructure, sagas/outbox |

<!-- Future topics:
- Event-Driven Architecture — decoupling services with events, event sourcing, event buses
- CQRS — separating read and write models for scalability
- Service-Level Resilience — service mesh, regional failover, cells, and blast-radius control
-->

---

## Reading Order

**Working result by entry 2**: run one business action through both API- and worker-shaped entry
points, then explain why neither transport owns the action.

1. **Do—diagnose the coupled route:** [Why Hexagonal Architecture](hexagonal_architecture/01_why_hexagonal_architecture.md) follows one AI endpoint, applies concrete change requests, and assigns the resulting responsibilities.
2. **Do—build the separated result:** [Build one vertical slice](hexagonal_architecture/02_build_one_vertical_slice.md) and observe the same action produce results for two inbound adapters.
3. **Understand, then harden:** trace the dependency rule and follow the [Hexagonal Architecture index](hexagonal_architecture/README.md) into contracts, lifecycle, process boundaries, AI, testing, or migration.

**Stop here if** the runnable slice gives your service a stable, testable application boundary.
Continue into [Long-Running Tasks](long_running_tasks/README.md) when work must outlive an HTTP
request and needs durable lifecycle, delivery, or recovery mechanisms.

---

## Prerequisites

Read these before diving into architecture patterns:

- **[fundamentals/concurrency/](../fundamentals/concurrency/)** — async/await, threading, multiprocessing, the GIL
- **[background_work/](../background_work/)** — Dramatiq, task queues, scheduling
- **[infrastructure/redis/](../infrastructure/redis/)** — caching, pub/sub, data structures
- **[fundamentals/fastapi/](../fundamentals/fastapi/)** — HTTP APIs, dependency injection, middleware
