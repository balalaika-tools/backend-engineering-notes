# Alembic — Database Migrations

Everything about Alembic: setup, autogenerate, data migrations, branching, multi-database, CI/CD integration, and production deployment strategies.

> Files `03` and `04` cover basic Alembic setup in the context of SQLAlchemy sync and async. This file is the deep-dive — the stuff you need when your team grows and your schema gets complex.

---

## 1. Mental Model

```
Your SQLAlchemy Models (Python)         Your Database (PostgreSQL)
         │                                        │
         ▼                                        ▼
    target_metadata                         current schema
         │                                        │
         └────────── alembic autogenerate ────────┘
                            │
                            ▼
              Migration Script (versions/*.py)
                            │
                     alembic upgrade
                            │
                            ▼
              Database schema matches models
```

Alembic is **not** Django's auto-migration system. It generates a *draft* that you review and edit. Autogenerate is a convenience, not an authority.

---

## 2. Setup

### Install

```bash
pip install alembic
```

### Initialize

```bash
# Async project (FastAPI + asyncpg)
alembic init -t async alembic

# Sync project
alembic init alembic
```

This creates:

```
project/
├── alembic/
│   ├── env.py              ← connects to DB, runs migrations
│   ├── script.py.mako      ← template for new migration files
│   └── versions/           ← migration scripts live here
├── alembic.ini             ← configuration file
```

### Configure `alembic.ini`

```ini
# alembic.ini
# Leave blank — set from app config in env.py to avoid hardcoded credentials
sqlalchemy.url =

# Useful: set the migration file naming template
# file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s
```

The `file_template` setting controls migration filenames. The default is just the revision hash + slug. Adding a timestamp makes the `versions/` folder chronologically sorted:

```
versions/
├── 2025_03_15_1430-a1b2c3_add_users_table.py
├── 2025_03_17_0900-d4e5f6_add_posts_table.py
├── 2025_03_20_1100-g7h8i9_add_tags.py
```

### Configure `env.py` (Async)

```python
# alembic/env.py
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Import Base so Alembic sees all table metadata
from app.models import Base
from app.config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without connecting to the database."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

> **Critical:** Every model file must be imported somewhere that `Base` touches. The standard pattern is `app/models/__init__.py` that imports all models:
>
> ```python
> # app/models/__init__.py
> from .base import Base
> from .user import User
> from .post import Post
> from .tag import Tag
> ```
> If a model isn't imported here, Alembic won't see it and autogenerate will skip it.

---

## 3. Core Commands

```bash
# ── Generate ──────────────────────────────────────────────

# Autogenerate from model diff
alembic revision --autogenerate -m "add users table"

# Empty migration (for custom SQL, data migrations)
alembic revision -m "backfill user tiers"

# ── Apply ─────────────────────────────────────────────────

alembic upgrade head          # apply all pending
alembic upgrade +1            # apply next one
alembic upgrade a1b2c3        # apply up to specific revision

# ── Rollback ──────────────────────────────────────────────

alembic downgrade -1          # roll back one
alembic downgrade a1b2c3      # roll back to specific revision
alembic downgrade base        # roll back everything

# ── Inspect ───────────────────────────────────────────────

alembic current               # what revision is the DB at?
alembic history --verbose     # full migration chain
alembic heads                 # current head revision(s)
alembic branches              # show if there are multiple heads

# ── Preview ───────────────────────────────────────────────

alembic upgrade head --sql    # show SQL without running it
```

---

## 4. Autogenerate — What It Detects (and Doesn't)

| Detected | Not Detected |
|----------|-------------|
| Table create / drop | Table rename |
| Column add / drop | Column rename |
| Column type change | Enum value additions |
| Nullable change | CHECK constraint changes |
| Index add / drop | Custom SQL functions / triggers |
| Foreign key add / drop | Row-level security policies |
| Unique constraint changes | Sequence changes |
| Server default changes* | Partial indexes (sometimes) |

\* Server-default comparison is **off by default** — enable it with `compare_server_default=True` (and, similarly, `compare_type=True` for stricter type diffing) when calling `context.configure(...)` in `env.py`.

**Always review autogenerated migrations.** They are a starting point, not a finished product.

### Handling Renames

Autogenerate sees a rename as "drop old + create new" — you'll lose data. Fix it manually:

```python
def upgrade() -> None:
    # Autogenerate would do: drop_column("name") + add_column("full_name")
    # Instead:
    op.alter_column("users", "name", new_column_name="full_name")


def downgrade() -> None:
    op.alter_column("users", "full_name", new_column_name="name")
```

---

## 5. Data Migrations

Schema migrations change structure. Data migrations change content. Alembic handles both.

### Backfill Pattern

```python
"""backfill user tiers

Revision ID: c3d4e5f6
Revises: b2c3d4e5
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6"
down_revision = "b2c3d4e5"


def upgrade() -> None:
    # Use raw SQL — ORM models may change later, but this migration is frozen in time
    op.execute("UPDATE users SET tier = 'free' WHERE tier IS NULL")


def downgrade() -> None:
    op.execute("UPDATE users SET tier = NULL WHERE tier = 'free'")
```

> **Rule:** Never import ORM models in migration files. Models evolve; migrations are immutable snapshots. Use `op.execute()` with raw SQL or `sa.table()` / `sa.column()` for type-safe references:

```python
from alembic import op
import sqlalchemy as sa

# Lightweight table reference — no dependency on the ORM model
users = sa.table("users", sa.column("id", sa.Integer), sa.column("tier", sa.String))

def upgrade() -> None:
    op.execute(users.update().where(users.c.tier == None).values(tier="free"))
```

### The Three-Step Column Migration

Adding a NOT NULL column to a table with existing data:

```python
"""add tier column to users

Step 1: add nullable column (instant, no lock)
Step 2: backfill data
Step 3: set NOT NULL
"""
from alembic import op
import sqlalchemy as sa

def upgrade() -> None:
    # Step 1: nullable column — fast, no table rewrite
    op.add_column("users", sa.Column("tier", sa.String(20), nullable=True))

    # Step 2: backfill existing rows
    op.execute("UPDATE users SET tier = 'free' WHERE tier IS NULL")

    # Step 3: now safe to add NOT NULL
    op.alter_column("users", "tier", nullable=False, server_default="free")


def downgrade() -> None:
    op.drop_column("users", "tier")
```

For **very large tables** (millions of rows), split this into separate migrations so step 2 can run in batches outside of a transaction:

```python
# Migration 1: add nullable column
# Migration 2: backfill (potentially run manually with batching)
# Migration 3: add NOT NULL constraint
```

---

## 6. Handling Enums

PostgreSQL enums are a common pain point — Alembic can't add values to existing enums automatically.

### Adding a Value to an Existing Enum

```python
"""add 'enterprise' to user_tier enum

Alembic autogenerate won't detect this.
"""
from alembic import op

def upgrade() -> None:
    # PostgreSQL 10+: ALTER TYPE ... ADD VALUE is not transactional
    # Must run outside of a transaction block
    op.execute("ALTER TYPE user_tier ADD VALUE IF NOT EXISTS 'enterprise'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values
    # You'd need to recreate the type — usually not worth it
    pass
```

> **Gotcha:** `ALTER TYPE ... ADD VALUE` cannot run inside a transaction in PostgreSQL. If Alembic wraps migrations in a transaction (the default), you need to break it out.

The cleanest fix is to disable per-migration transactions for this revision. In `env.py`:

```python
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    transaction_per_migration=True,  # one txn per migration, so an AUTOCOMMIT migration is possible
)
```

Then inside the migration:

```python
from alembic import op

def upgrade() -> None:
    # Run outside any transaction — requires AUTOCOMMIT isolation level.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE user_tier ADD VALUE IF NOT EXISTS 'enterprise'")
```

The old `COMMIT` / `BEGIN` trick below works in practice but leaves the migration state fragile if a later statement fails — prefer `autocommit_block()`.

```python
def upgrade_legacy() -> None:
    op.execute("COMMIT")
    op.execute("ALTER TYPE user_tier ADD VALUE IF NOT EXISTS 'enterprise'")
    op.execute("BEGIN")
```

### Creating a New Enum Column

```python
def upgrade() -> None:
    # Create the type explicitly
    user_tier = sa.Enum("free", "pro", "enterprise", name="user_tier")
    user_tier.create(op.get_bind(), checkfirst=True)

    op.add_column("users", sa.Column("tier", user_tier, nullable=True))
```

---

## 7. Branching and Merging

When multiple developers create migrations in parallel, you get **multiple heads**.

### Detecting the Problem

```bash
$ alembic heads
a1b2c3 (head)   # developer A's migration
d4e5f6 (head)   # developer B's migration

$ alembic upgrade head
ERROR: Multiple head revisions are present; please specify a specific target
```

### Fixing: Merge Migration

```bash
alembic merge -m "merge heads" a1b2c3 d4e5f6
```

This creates a merge migration:

```python
"""merge heads

Revision ID: m1n2o3
Revises: a1b2c3, d4e5f6
"""

revision = "m1n2o3"
down_revision = ("a1b2c3", "d4e5f6")  # tuple = merge point


def upgrade() -> None:
    pass  # no-op unless you need conflict resolution


def downgrade() -> None:
    pass
```

### Prevention: Team Workflow

```
1. Pull main before creating a migration
2. Create migration
3. Pull main again — if a new migration appeared, merge heads
4. Push
```

Or enforce a linear migration chain in CI:

```bash
# CI check: fail if there are multiple heads
heads=$(alembic heads | wc -l)
if [ "$heads" -gt 1 ]; then
    echo "ERROR: Multiple Alembic heads detected. Run: alembic merge -m 'merge heads'"
    exit 1
fi
```

---

## 8. Stamping

`alembic stamp` sets the database's recorded revision **without running any migrations**. Useful for:

- Bringing an existing database under Alembic control
- Recovering from a broken migration state

```bash
# Mark the database as being at revision a1b2c3 (without running anything)
alembic stamp a1b2c3

# Mark as fully up to date
alembic stamp head

# Mark as having no migrations applied
alembic stamp base
```

### Adopting Alembic on an Existing Database

```bash
# 1. Initialize Alembic
alembic init -t async alembic

# 2. Configure env.py (import your models, set URL)

# 3. Generate the "initial" migration from your models
alembic revision --autogenerate -m "initial schema"

# 4. Stamp — the schema already exists, so don't run the migration
alembic stamp head

# 5. Future migrations run normally
alembic revision --autogenerate -m "add new feature"
alembic upgrade head  # only runs the new migration
```

---

## 9. Multi-Database Migrations

When your app talks to multiple databases (e.g., main + analytics).

### Setup with Named Sections

```ini
# alembic.ini
[alembic]
script_location = alembic/main

[analytics]
script_location = alembic/analytics
```

```bash
# Initialize each
alembic init alembic/main
alembic init alembic/analytics
```

### Separate env.py per Database

Each section gets its own `env.py` that connects to its respective database.

```bash
# Run migrations for a specific database
alembic -n analytics upgrade head
alembic -n alembic upgrade head   # default section
```

---

## 10. Testing Migrations

### Test: Upgrade → Downgrade → Upgrade

```python
import subprocess

def test_migrations_roundtrip():
    """Verify migrations can go up, down, and up again cleanly."""
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    subprocess.run(["alembic", "downgrade", "base"], check=True)
    subprocess.run(["alembic", "upgrade", "head"], check=True)
```

### Test: No Pending Migrations

Catch cases where someone changed a model but forgot to generate a migration.

```python
import subprocess

def test_no_pending_migrations():
    """Fail if models are out of sync with migrations."""
    # Apply all existing migrations
    subprocess.run(["alembic", "upgrade", "head"], check=True)

    # Try to autogenerate — should produce an empty migration
    result = subprocess.run(
        ["alembic", "revision", "--autogenerate", "-m", "test_check"],
        capture_output=True, text=True, check=True,
    )

    # Read the generated file and check it's empty
    # (or use alembic's check command if available)
    assert "pass" in result.stdout or "No changes detected" in result.stderr

    # Clean up the generated file
    subprocess.run(["alembic", "downgrade", "-1"], check=True)
```

### Alembic `check` Command (Alembic 1.9+)

```bash
# Fails with exit code 1 if models and migrations are out of sync
alembic check
```

Add this to CI:

```yaml
# .github/workflows/ci.yml
- name: Check for missing migrations
  run: alembic check
```

---

## 11. CI/CD Integration

### GitHub Actions Example

```yaml
name: Database CI

on: [push, pull_request]

jobs:
  migrations:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: testdb
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432

    env:
      DATABASE_URL: postgresql+asyncpg://test:test@localhost:5432/testdb

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - run: pip install -r requirements.txt

      - name: Check for multiple heads
        run: |
          heads=$(alembic heads | wc -l)
          [ "$heads" -le 1 ] || (echo "Multiple heads!" && exit 1)

      - name: Check no pending migrations
        run: alembic check

      - name: Upgrade to head
        run: alembic upgrade head

      - name: Downgrade to base
        run: alembic downgrade base

      - name: Upgrade again (roundtrip test)
        run: alembic upgrade head
```

---

## 12. Production Deployment

### Migration in Docker Entrypoint

```dockerfile
# Dockerfile
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

```bash
#!/bin/bash
# entrypoint.sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Migration as a Separate Init Container (Kubernetes)

```yaml
# deployment.yaml
initContainers:
  - name: migrate
    image: myapp:latest
    command: ["alembic", "upgrade", "head"]
    env:
      - name: DATABASE_URL
        valueFrom:
          secretKeyRef:
            name: db-credentials
            key: url

containers:
  - name: app
    image: myapp:latest
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0"]
```

> **Init container is preferred** over entrypoint migration: only one pod runs the migration, and the app pods wait until it succeeds.

### Safe Production Checklist

| Step | Command / Action |
|------|------------------|
| Preview the SQL | `alembic upgrade head --sql > preview.sql` |
| Take a DB snapshot | `pg_dump` or cloud snapshot before migrating |
| Run in a staging environment first | Apply to staging, run tests, then production |
| Check for long locks | Avoid `ALTER TABLE ... ADD COLUMN ... NOT NULL` on large tables without the three-step pattern |
| Monitor migration time | Time the migration; alert if it exceeds expected duration |
| Have a rollback plan | Know which `alembic downgrade` command to run, or restore from snapshot |

### Avoiding Locks on Large Tables

Some operations lock the entire table. On a production table with millions of rows, this blocks all reads and writes.

**Safe operations (no lock or short lock):**
- `ADD COLUMN ... NULL` (no default)
- `ADD COLUMN ... DEFAULT x` (PostgreSQL 11+ — instant, stored in catalog)
- `DROP COLUMN` (marks as invisible, space reclaimed lazily)
- `CREATE INDEX CONCURRENTLY`

**Dangerous operations (full table lock):**
- `ALTER COLUMN ... SET NOT NULL` (validates every row)
- `ALTER COLUMN ... TYPE ...` (rewrites the table)
- `ADD COLUMN ... DEFAULT x` (PostgreSQL < 11)
- `CREATE INDEX` (without CONCURRENTLY)

```python
# Safe: create index without locking the table
def upgrade() -> None:
    # CONCURRENTLY can't run inside a transaction
    op.execute("COMMIT")
    op.create_index(
        "ix_orders_created_at",
        "orders",
        ["created_at"],
        postgresql_concurrently=True,
    )
    op.execute("BEGIN")
```

> **PostgreSQL 12+:** `ALTER TABLE ... ADD CONSTRAINT ... NOT NULL NOT VALID` + `VALIDATE CONSTRAINT` is the safe way to add NOT NULL to large tables. The `NOT VALID` part skips the full table scan, and `VALIDATE CONSTRAINT` acquires only a `SHARE UPDATE EXCLUSIVE` lock.

---

## 13. Common Operations Reference

### Add a Column

```python
op.add_column("users", sa.Column("bio", sa.Text(), nullable=True))
```

### Drop a Column

```python
op.drop_column("users", "bio")
```

### Rename a Column

```python
op.alter_column("users", "name", new_column_name="full_name")
```

### Change Column Type

```python
op.alter_column(
    "users", "age",
    type_=sa.SmallInteger(),
    existing_type=sa.Integer(),
)
```

### Add an Index

```python
op.create_index("ix_users_email", "users", ["email"], unique=True)
```

### Add a Foreign Key

```python
op.create_foreign_key(
    "fk_posts_author_id",
    "posts", "users",
    ["author_id"], ["id"],
    ondelete="CASCADE",
)
```

### Create a Table

```python
op.create_table(
    "tags",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("name", sa.String(50), nullable=False, unique=True),
    sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
)
```

### Drop a Table

```python
op.drop_table("tags")
```

### Execute Raw SQL

```python
op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
op.execute("UPDATE users SET status = 'active' WHERE status IS NULL")
```

### Bulk Insert (in data migration)

```python
from alembic import op
import sqlalchemy as sa
from datetime import datetime

roles_table = sa.table(
    "roles",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("created_at", sa.DateTime),
)

def upgrade() -> None:
    op.bulk_insert(roles_table, [
        {"id": 1, "name": "admin", "created_at": datetime.utcnow()},
        {"id": 2, "name": "editor", "created_at": datetime.utcnow()},
        {"id": 3, "name": "viewer", "created_at": datetime.utcnow()},
    ])
```

---

## 14. Troubleshooting

### "Target database is not up to date"

```bash
$ alembic revision --autogenerate -m "new migration"
ERROR: Target database is not up to date.
```

**Fix:** Apply pending migrations first, then generate.

```bash
alembic upgrade head
alembic revision --autogenerate -m "new migration"
```

### "Can't locate revision"

```bash
$ alembic upgrade head
ERROR: Can't locate revision identified by 'a1b2c3'
```

**Cause:** A migration file was deleted but the `alembic_version` table still references it.

**Fix:**

```bash
# Check what the DB thinks it's at
alembic current

# Stamp to a known good revision
alembic stamp <last_known_good_revision>

# Or stamp to base and re-upgrade
alembic stamp base
alembic upgrade head
```

### "Multiple heads"

```bash
$ alembic upgrade head
ERROR: Multiple head revisions are present
```

**Fix:**

```bash
alembic merge -m "merge heads"
alembic upgrade head
```

### Migration generated but nothing changed

Autogenerate might create empty migrations if:
- Your models import `Base` from the wrong location
- `env.py` doesn't import all model modules
- You have tables created outside of SQLAlchemy (Alembic compares against `target_metadata` only)

Check `env.py`:
```python
# This must import ALL model modules
from app.models import Base  # make sure __init__.py imports everything
target_metadata = Base.metadata
```

### Autogenerate creates duplicate table operations

When models are imported multiple times through different paths, metadata can get duplicated.

**Fix:** Import `Base` from exactly one location. Every model inherits from the same `Base`.
