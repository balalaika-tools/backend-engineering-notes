# Logging

> Python's `logging` module — from basic setup to handler pipelines, logger hierarchies, and production patterns.

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [01_basics.md](01_basics.md) | Basics | `basicConfig`, log levels, file logging, exceptions, rotation |
| [02_hierarchy_and_propagation.md](02_hierarchy_and_propagation.md) | Hierarchy & Propagation | Logger name tree, parent-child, `propagate`, double-emit |
| [03_handlers_and_formatters.md](03_handlers_and_formatters.md) | Handlers & Formatters | `LogRecord → Handler → Formatter` pipeline, built-in handlers, custom formatters |
| [04_patterns.md](04_patterns.md) | Patterns | Universal logging, per-subfolder logging, mixing both, best practices |

---

## Reading Order

1. **Basics** — `basicConfig`, log levels, file output, exceptions
2. **Hierarchy & Propagation** — understand the tree before writing any handler code
3. **Handlers & Formatters** — the full pipeline: where records go and how they look
4. **Patterns** — per-subfolder vs universal vs both; `setup_logging()` utility pattern

---

## Prerequisites

- [decorators.md](../decorators.md) — helpful for understanding `functools.wraps` used in logging wrappers
- For production structured logging, continue to [structlog_guide.md](../structlog_guide.md)
