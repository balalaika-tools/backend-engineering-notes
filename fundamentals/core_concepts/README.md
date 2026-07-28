# Core Python Concepts

> Fundamental Python concepts every backend developer needs to understand.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org)
[![structlog](https://img.shields.io/badge/structlog-latest-4B8BBE.svg)](https://www.structlog.org)
[![pydantic-settings](https://img.shields.io/badge/pydantic--settings-2.x-E92063.svg)](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [typing.md](typing.md) | Typing | Runtime vs static contracts, `Optional`, `TypedDict`, generics, protocols, `ParamSpec`, `Annotated` |
| [context_managers.md](context_managers.md) | Context Managers | Resource lifetimes, protocol mechanics, partial setup, async managers, `ExitStack` |
| [decorators.md](decorators.md) | Decorators | Rebinding mental model, closures, `wraps`, parameters, async wrappers, stacking |
| [exceptions.md](exceptions.md) | Exceptions | Stack unwinding, precise catches, chaining, domain translation, boundaries, exception groups |
| [logging/](logging/README.md) | Logging | Admission and propagation mechanics, handler routing, queues, framework and process ownership |
| [structlog_guide.md](structlog_guide.md) | Structured Logging | Processor pipelines, unified stdlib output, FastAPI request context, testing |
| [configuration.md](configuration.md) | Configuration | Source precedence, pydantic-settings, `.env`, secret delivery, validation, caching |
| [signals.md](signals.md) | Unix Signals | Python delivery mechanics, bounded graceful shutdown, asyncio, Uvicorn, containers |

---

## Reading Order

1. **Typing** — learn the contract vocabulary used by every later example
2. **Context Managers** — understand resource lifetimes before database/client examples
3. **Decorators** — expand `@` syntax into ordinary function rebinding and closures
4. **Exceptions** — follow failures through layers and decide where policy belongs
5. **Logging** — make success and failure observable without duplicate or missing records
6. **Structured Logging** — turn records into searchable events with request context
7. **Configuration** — validate deployment input and secret delivery at startup
8. **Signals** — coordinate application cleanup with the process/platform lifecycle

For request-scoped state and async-safe context propagation, read [concurrency/async/03_contextvars.md](../concurrency/async/03_contextvars.md).

---

## Prerequisites

- Basic Python (functions, classes, imports, and function calls)
