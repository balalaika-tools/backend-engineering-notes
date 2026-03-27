# Database

> Everything you need to work with PostgreSQL in production Python backends — raw SQL, drivers, ORMs, async patterns, and connection pooling.

[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-336791.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-D71F00.svg)](https://www.sqlalchemy.org)
[![asyncpg](https://img.shields.io/badge/asyncpg-0.29+-2E6FA3.svg)](https://github.com/MagicStack/asyncpg)
[![Alembic](https://img.shields.io/badge/Alembic-1.x-6BA81E.svg)](https://alembic.sqlalchemy.org)

---

## Contents

| File | Topic | What you'll learn |
|------|-------|-------------------|
| [01_databases_and_schemas.md](01_databases_and_schemas.md) | Foundations | PostgreSQL vs SQLite, schemas, data types, constraints, indexes, transactions, ACID, SQL basics |
| [02_python_drivers.md](02_python_drivers.md) | Drivers | psycopg3 (sync + async), asyncpg — raw database access, bulk insert, COPY protocol |
| [03_sqlalchemy_orm.md](03_sqlalchemy_orm.md) | ORM | SQLAlchemy 2.0 models, relationships, sync session patterns, basic Alembic setup |
| [04_async_sqlalchemy.md](04_async_sqlalchemy.md) | Async SQLAlchemy | Async engine/session, FastAPI integration, CRUD, transactions, eager loading, common mistakes |
| [05_connection_pooling.md](05_connection_pooling.md) | Connection Pooling | Pool sizing math, SQLAlchemy pool config, PgBouncer, asyncpg pool, failure modes |
| [06_alembic.md](06_alembic.md) | Alembic Deep-Dive | Data migrations, enum handling, branching/merging, stamping, multi-DB, CI/CD, production deployment, lock-safe patterns |

---

## Learning Path

**New to databases** → Start with `01` (foundations) then `03` (ORM concepts).

**Know SQL, new to Python DB libs** → Start with `02` (drivers) or `03` (SQLAlchemy).

**Building a FastAPI app** → Read `03` for models, then `04` for async patterns, then `06` for migrations.

**Performance / production concerns** → Read `05` for connection pooling, check the "raw asyncpg" section in `04`.

**Migration workflows** → Read `03` section 7 for basics, then `06` for the full deep-dive (data migrations, CI/CD, production safety).

**Something breaking** → Jump to "Common Mistakes" in `04`, "Failure Modes" in `05`, or "Troubleshooting" in `06`.

---

## Key Decisions at a Glance

| Question | Answer |
|----------|--------|
| PostgreSQL or SQLite? | PostgreSQL everywhere, including local dev (Docker) |
| Which async driver? | `asyncpg` (max perf) or `psycopg[async]` (versatile) |
| ORM or raw SQL? | SQLAlchemy ORM for app code; raw driver for bulk ops and hot paths |
| Sync or async SQLAlchemy? | Async for FastAPI. Sync for scripts and CLIs. |
| `lazy="select"` or `lazy="raise"`? | Always `lazy="raise"` in async. Forces explicit loading, prevents hidden bugs. |
| `expire_on_commit`? | Always `False` for async sessions. |
| Connection pool size? | `(DB max_connections × 0.9) / app_instances` — see `05` for full formula |
| PgBouncer? | Use it when you have many app instances and need to limit server connections |

---

## Prerequisites

- [Concurrency & Async](../concurrency/README.md) — understand event loops before async SQLAlchemy
- [FastAPI Dependency Injection](../fastapi/02_dependency_injection.md) — `Depends(get_db)` pattern
