# Core Python Concepts

> Fundamental Python concepts every backend developer needs to understand.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org)
[![structlog](https://img.shields.io/badge/structlog-latest-4B8BBE.svg)](https://www.structlog.org)
[![pydantic-settings](https://img.shields.io/badge/pydantic--settings-2.x-E92063.svg)](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [decorators.md](decorators.md) | Decorators | `@` syntax, `functools.wraps`, parameterized decorators |
| [exceptions.md](exceptions.md) | Exceptions | Propagation, `raise` variants, `raise from`, production patterns |
| [logging/](logging/README.md) | Logging | Logger hierarchy, `propagate`, handler/formatter pipeline, per-module vs universal |
| [structlog_guide.md](structlog_guide.md) | Structured Logging | structlog, processors, FastAPI integration, contextvars |
| [configuration.md](configuration.md) | Configuration | pydantic-settings, `.env`, secrets, validation |
| [contextvars.md](contextvars.md) | ContextVars | Request-scoped state, async-safe thread-locals, propagation patterns |

---

## Reading Order

1. **Decorators** — understand how functions wrap functions
2. **Exceptions** — understand error propagation and handling
3. **Logging** — stdlib logging: basics → hierarchy → handlers → patterns
4. **Structured Logging** — upgrade to structlog for production
5. **Configuration** — manage settings and secrets
6. **ContextVars** — request-scoped state that flows through async code

---

## Prerequisites

- Basic Python (functions, classes, context managers)
