# Database

Everything you need to work with PostgreSQL in production Python backends. Covers raw SQL, drivers, ORMs, async patterns, and connection pooling — from first principles to production-ready code.

---

## Contents

| File | Topic | What you'll learn |
|------|-------|-------------------|
| [01_databases_and_schemas.md](01_databases_and_schemas.md) | Foundations | PostgreSQL vs SQLite, schemas, data types, constraints, indexes, transactions, ACID, SQL basics, advanced PG features |
| [02_python_drivers.md](02_python_drivers.md) | Drivers | psycopg3 (sync + async), asyncpg — raw database access, bulk insert, COPY protocol, when to skip the ORM |
| [03_sqlalchemy_orm.md](03_sqlalchemy_orm.md) | ORM | What is an ORM, SQLAlchemy 2.0 models, relationships, Alembic migrations, sync session patterns |
| [04_async_sqlalchemy.md](04_async_sqlalchemy.md) | Async SQLAlchemy | Async engine/session setup, FastAPI integration, CRUD, transactions, eager loading, raw asyncpg, common mistakes |
| [05_connection_pooling.md](05_connection_pooling.md) | Connection Pooling | Pool sizing math, SQLAlchemy pool config, PgBouncer, asyncpg pool, monitoring, failure modes, timeouts |

---

## Learning Path

**New to databases** → Start with `01` (foundations) then `03` (ORM concepts).

**Know SQL, new to Python DB libs** → Start with `02` (drivers) or `03` (SQLAlchemy).

**Building a FastAPI app** → Read `03` for models and migrations, then `04` for async patterns.

**Performance / production concerns** → Read `05` for connection pooling, check the "raw asyncpg" section in `04`.

**Something breaking** → Jump to "Common Mistakes" in `04`, or "Failure Modes" in `05`.

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
