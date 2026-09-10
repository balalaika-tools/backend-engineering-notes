# Core Python Concepts

> Fundamental Python concepts every backend developer needs to understand.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org)
[![structlog](https://img.shields.io/badge/structlog-latest-4B8BBE.svg)](https://www.structlog.org)
[![pydantic-settings](https://img.shields.io/badge/pydantic--settings-2.x-E92063.svg)](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [typing_workflow.md](typing_workflow.md) | Type-checking Workflow | Run mypy, own repository configuration, interpret diagnostics, enforce the same scope in CI |
| [typing.md](typing.md) | Typing | Runtime vs static contracts, `TypedDict`, generics, protocols, abstract inputs, `NewType`, overloads, narrowing |
| [iterators_and_generators.md](iterators_and_generators.md) | Iterators & Generators | Iteration protocols, suspension, one-shot streams, cleanup, async generators |
| [data_model_choices.md](data_model_choices.md) | Data Model Choices | Standard dataclasses vs Pydantic dataclasses vs `BaseModel` by runtime and boundary contract |
| [context_managers.md](context_managers.md) | Context Managers | Resource lifetimes, protocol mechanics, partial setup, async managers, `ExitStack` |
| [decorators.md](decorators.md) | Decorators | Rebinding mental model, closures, `wraps`, parameters, async wrappers, stacking |
| [exceptions.md](exceptions.md) | Exceptions | Stack unwinding, precise catches, chaining, domain translation, boundaries, exception groups |
| [logging/](logging/README.md) | Logging | Admission and propagation mechanics, handler routing, queues, framework and process ownership |
| [structlog_guide.md](structlog_guide.md) | Structured Logging | Processor pipelines, unified stdlib output, FastAPI request context, testing |
| [configuration.md](configuration.md) | Configuration | Source precedence, pydantic-settings, `.env`, secret delivery, validation, caching |
| [signals.md](signals.md) | Unix Signals | Python delivery mechanics, bounded graceful shutdown, asyncio, Uvicorn, containers |

---

## Reading Order

**Working result by entry 2**: run a type checker against one contract, observe both its success and
failure output, and explain why the hint itself does not enforce runtime values.

1. **Do:** [Type-checking Workflow](typing_workflow.md) — run mypy and prove that the intended files are actually checked.
2. **Understand:** [Typing](typing.md) — choose the smallest useful contract and keep runtime validation separate.
3. **Trace lazy work:** [Iterators and Generators](iterators_and_generators.md) — follow one-shot iteration, suspension, early close, and async cleanup.
4. **Own a lifetime:** [Context Managers](context_managers.md) — trace acquisition, use, and cleanup, including partial setup failure.
5. **Choose data ownership:** [Data Model Choices](data_model_choices.md), then extend the language model with [Decorators](decorators.md) and [Exceptions](exceptions.md).
6. **Harden the process boundary:** [Logging](logging/README.md), [Structured Logging](structlog_guide.md), [Configuration](configuration.md), and [Signals](signals.md).

**Stop after step 2** if you only needed enforceable Python contracts. Continue through step 4 when
you consume lazy streams or own setup/teardown. Continue into step 6 when you own application
startup, observability, configuration, or shutdown.

For request-scoped state and async-safe context propagation, read [concurrency/async/03_contextvars.md](../concurrency/async/03_contextvars.md).

---

## Prerequisites

- Basic Python (functions, classes, imports, and function calls)
