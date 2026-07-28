# Logging

> Python's `logging` module — from the first record to hierarchy mechanics,
> handler routing, async-safe queues, framework integration, and production
> ownership.

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [01_basics.md](01_basics.md) | Basics | Record mental model, module loggers, levels, lazy formatting, exceptions, destinations |
| [02_hierarchy_and_propagation.md](02_hierarchy_and_propagation.md) | Hierarchy & Propagation | Effective levels, direct ancestor-handler propagation, `propagate`, duplicate diagnosis |
| [03_handlers_and_formatters.md](03_handlers_and_formatters.md) | Handlers & Formatters | Full pipeline, stream/file behavior, filters, buffering, async `QueueHandler` lifecycle |
| [04_patterns.md](04_patterns.md) | Patterns | `basicConfig` vs `dictConfig`, dedicated subtrees, libraries, frameworks, multi-process limits |

---

## Reading Order

1. **Basics** — understand what a log call creates and who owns configuration
2. **Hierarchy & Propagation** — learn the admission gate and ancestor-handler walk
3. **Handlers & Formatters** — route, filter, render, queue, and shut down records
4. **Patterns** — choose a topology for scripts, services, frameworks, and libraries

---

## Prerequisites

- [exceptions.md](../exceptions.md) — know where failures should be handled and logged
- For production structured logging, continue to [structlog_guide.md](../structlog_guide.md)
