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
| [Long-Running Tasks](long_running_tasks/README.md) | Handling requests that take seconds to hours: orchestration, worker patterns, client delivery, infrastructure, sagas/outbox |

<!-- Future topics:
- Event-Driven Architecture — decoupling services with events, event sourcing, event buses
- CQRS — separating read and write models for scalability
- Service-Level Resilience — service mesh, regional failover, cells, and blast-radius control
-->

---

## Prerequisites

Read these before diving into architecture patterns:

- **[fundamentals/concurrency/](../fundamentals/concurrency/)** — async/await, threading, multiprocessing, the GIL
- **[background_work/](../background_work/)** — Dramatiq, task queues, scheduling
- **[infrastructure/redis/](../infrastructure/redis/)** — caching, pub/sub, data structures
- **[fundamentals/fastapi/](../fundamentals/fastapi/)** — HTTP APIs, dependency injection, middleware
