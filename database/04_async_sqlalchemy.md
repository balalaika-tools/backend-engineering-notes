# Async Database Access — SQLAlchemy & asyncpg

> **Core idea**: SQLAlchemy async is not "sync SQLAlchemy with `await` sprinkled in." It changes how you think about sessions, loading, and object state. For raw performance, asyncpg gives you direct access to PostgreSQL's binary protocol.

---

## 1. Mental Model

### Sync vs Async — The Fundamental Difference

In sync SQLAlchemy, the ORM does invisible I/O constantly. Access `user.posts` and it silently hits the database. This works because the thread blocks until the query returns.

In async SQLAlchemy, **every database access must be explicit**. The event loop cannot be blocked by invisible I/O. If you try, you get `MissingGreenlet` or `DetachedInstanceError`.

```
Sync:   user.posts  →  (hidden SQL query)  →  list of posts
Async:  user.posts  →  MissingGreenlet error (cannot do implicit I/O)
```

### What This Means in Practice

| Concern | Sync SQLAlchemy | Async SQLAlchemy |
|---------|----------------|------------------|
| Lazy loading | Works transparently | Raises exceptions |
| Session scope | Thread-local, implicit | Explicit, must be passed |
| Relationship access | Automatic SQL on attribute access | Must use `selectinload` / `joinedload` |
| Connection management | Thread pool handles it | Event loop — one connection blocks all if misused |
| Engine | `create_engine` | `create_async_engine` — wraps an async driver |

### The Driver Layer

Async SQLAlchemy does not talk to the database itself. It delegates to an async driver:

| Database | Async Driver | Install |
|----------|-------------|---------|
| PostgreSQL | `asyncpg` | `pip install asyncpg` |
| PostgreSQL | `psycopg[async]` | `pip install psycopg[binary]` |
| MySQL | `aiomysql` | `pip install aiomysql` |
| SQLite | `aiosqlite` | `pip install aiosqlite` |

The URL scheme changes:

```python
# Sync
"postgresql://user:pass@host/db"

# Async (asyncpg)
"postgresql+asyncpg://user:pass@host/db"

# Async (psycopg3)
"postgresql+psycopg://user:pass@host/db"
```

---

## 2. Engine and Session Setup

### The Engine

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/mydb"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,          # True logs all SQL — useful for dev, noisy in prod
    pool_size=5,         # connections held open at steady state
    max_overflow=10,     # additional connections under load (total max = 15)
    pool_pre_ping=True,  # verify connections are alive before using them
    pool_recycle=3600,   # recycle connections after 1 hour
)
```

**`create_async_engine` does NOT create connections yet.** It initializes the pool configuration. Connections are created lazily on first use.

### The Session Factory

```python
async_session = async_sessionmaker(
    engine,
    expire_on_commit=False,  # critical for async — explained below
)
```

### Why `expire_on_commit=False`

After a commit, SQLAlchemy expires all loaded attributes by default. In sync code, accessing an expired attribute triggers a lazy reload. In async code, that lazy reload raises an error.

```python
# expire_on_commit=True (default)
await session.commit()
print(user.name)  # MissingGreenlet — tries implicit I/O to reload

# expire_on_commit=False
await session.commit()
print(user.name)  # Works — uses the in-memory value
```

Set `expire_on_commit=False` on your session factory. If you need fresh data after a commit, explicitly `await session.refresh(obj)`.

---

## 3. Model Definition

### Modern Declarative Style (SQLAlchemy 2.0+)

```python
from datetime import datetime
from sqlalchemy import String, ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationship — NEVER access this lazily in async
    posts: Mapped[list["Post"]] = relationship(back_populates="author", lazy="raise")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str]
    published: Mapped[bool] = mapped_column(default=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    author: Mapped["User"] = relationship(back_populates="posts", lazy="raise")
```

### Key Decisions

| Pattern | What it does | When to use |
|---------|-------------|-------------|
| `Mapped[str]` | Type-annotated column, NOT NULL | Required fields |
| `Mapped[str \| None]` | Nullable column | Optional fields |
| `mapped_column(default=...)` | Python-side default | Values computed in app |
| `mapped_column(server_default=...)` | Database-side default | Timestamps, UUIDs |
| `lazy="raise"` | Raises error on lazy access | Always in async — makes bugs loud |
| `lazy="selectin"` | Eager loads via SELECT IN | When you always need the relation |

### Why `lazy="raise"` Is the Right Default for Async

```python
# lazy="select" (the default) — DANGEROUS in async
user = await session.get(User, 1)
user.posts  # MissingGreenlet — silent in sync, error in async

# lazy="raise" — SAFE
user = await session.get(User, 1)
user.posts  # raises InvalidRequestError immediately — clear error message
```

With `lazy="raise"`, you are forced to explicitly load relationships when you need them. This makes your queries predictable and your bugs obvious.

---

## 4. FastAPI Integration

### The `get_db` Dependency

```python
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
```

This gives you:

- One session per request
- Automatic cleanup when the request ends
- Session is closed even if the endpoint raises an exception

### Using It in Endpoints

```python
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel

app = FastAPI()


class UserCreate(BaseModel):
    email: str
    name: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    is_active: bool

    model_config = {"from_attributes": True}


@app.post("/users", response_model=UserResponse)
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db)):
    user = User(email=data.email, name=data.name)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
```

### Full Application Structure

```
app/
  __init__.py
  main.py          # FastAPI app, lifespan, routers
  database.py      # engine, async_session, get_db
  models.py        # Base, User, Post, etc.
  schemas.py       # Pydantic models
  routers/
    users.py
    posts.py
```

**`database.py`** — single source of truth for all DB configuration:

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from typing import AsyncGenerator

DATABASE_URL = "postgresql+asyncpg://user:pass@localhost:5432/mydb"

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
```

### Lifespan: Clean Shutdown

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — engine is already configured, connections created lazily
    yield
    # Shutdown — close all pooled connections
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
```

---

## 5. CRUD Patterns

### Create

```python
from sqlalchemy.ext.asyncio import AsyncSession

async def create_user(db: AsyncSession, email: str, name: str) -> User:
    user = User(email=email, name=name)
    db.add(user)
    await db.commit()
    await db.refresh(user)  # reload to get server-generated fields (id, created_at)
    return user
```

### Read — Single Record

```python
from sqlalchemy import select

async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
```

**`session.get()` for primary key lookups** — checks the identity map first (no SQL if already loaded):

```python
async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    return await db.get(User, user_id)
```

### Read — List with Filters

```python
async def list_users(
    db: AsyncSession,
    *,
    is_active: bool | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> list[User]:
    stmt = select(User)

    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    if search:
        stmt = stmt.where(User.name.ilike(f"%{search}%"))

    stmt = stmt.offset(offset).limit(limit).order_by(User.id)

    result = await db.execute(stmt)
    return list(result.scalars().all())
```

### Read — With Relationships

```python
from sqlalchemy.orm import selectinload

async def get_user_with_posts(db: AsyncSession, user_id: int) -> User | None:
    stmt = (
        select(User)
        .options(selectinload(User.posts))
        .where(User.id == user_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
```

### Update

```python
async def update_user(db: AsyncSession, user_id: int, **kwargs) -> User | None:
    user = await db.get(User, user_id)
    if user is None:
        return None

    for key, value in kwargs.items():
        setattr(user, key, value)

    await db.commit()
    await db.refresh(user)
    return user
```

**Bulk update without loading objects** — use this for batch operations:

```python
from sqlalchemy import update

async def deactivate_users(db: AsyncSession, user_ids: list[int]) -> int:
    stmt = (
        update(User)
        .where(User.id.in_(user_ids))
        .values(is_active=False)
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount
```

### Delete

```python
async def delete_user(db: AsyncSession, user_id: int) -> bool:
    user = await db.get(User, user_id)
    if user is None:
        return False

    await db.delete(user)
    await db.commit()
    return True
```

**Bulk delete**:

```python
from sqlalchemy import delete

async def purge_inactive_users(db: AsyncSession) -> int:
    stmt = delete(User).where(User.is_active == False)
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount
```

---

## 6. Transactions

### Default Behavior: Auto-Begin

SQLAlchemy 2.0 sessions use **auto-begin**. A transaction starts implicitly when you first execute a statement. It stays open until you `commit()` or `rollback()`.

```python
async with async_session() as session:
    session.add(User(email="a@b.com", name="Alice"))  # transaction begins
    await session.commit()                              # transaction commits
    # new transaction will begin on next operation
```

### Explicit Commit and Rollback

```python
async def transfer_credits(db: AsyncSession, from_id: int, to_id: int, amount: int):
    sender = await db.get(User, from_id)
    receiver = await db.get(User, to_id)

    if sender.credits < amount:
        raise ValueError("Insufficient credits")

    sender.credits -= amount
    receiver.credits += amount

    await db.commit()  # both changes committed atomically
```

If an exception occurs before `commit()`, the session is in a failed state. The `async with async_session()` context manager rolls back automatically on exit.

### Manual Rollback

```python
async def risky_operation(db: AsyncSession):
    try:
        db.add(User(email="new@test.com", name="New"))
        await db.flush()  # sends SQL but does NOT commit

        # validation logic...
        if something_wrong:
            await db.rollback()
            return None

        await db.commit()
    except Exception:
        await db.rollback()
        raise
```

### `flush()` vs `commit()`

| Operation | What it does | Transaction state |
|-----------|-------------|-------------------|
| `flush()` | Sends pending SQL to the database | Still inside transaction |
| `commit()` | Flushes, then commits the transaction | Transaction ends |
| `rollback()` | Discards all changes since last commit | Transaction ends |

`flush()` is useful when you need a server-generated value (like an auto-increment ID) mid-transaction but don't want to commit yet.

```python
user = User(email="test@test.com", name="Test")
db.add(user)
await db.flush()       # user.id is now populated
print(user.id)         # e.g., 42
post = Post(title="Hello", content="World", author_id=user.id)
db.add(post)
await db.commit()      # both user and post committed atomically
```

### Nested Transactions (Savepoints)

Use `begin_nested()` to create savepoints inside a transaction. This lets you roll back part of the work without aborting the whole transaction.

```python
async def import_records(db: AsyncSession, records: list[dict]) -> list[str]:
    errors = []

    for record in records:
        try:
            async with db.begin_nested():  # SAVEPOINT
                user = User(**record)
                db.add(user)
                await db.flush()
        except IntegrityError as e:
            # savepoint rolled back, but outer transaction is fine
            errors.append(f"Skipped {record['email']}: {e}")

    await db.commit()  # commit all successful inserts
    return errors
```

### The `get_db` Dependency — Transaction Strategy

Two common strategies:

**Strategy A: Endpoint controls commits (recommended)**

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
        # no auto-commit — endpoint is responsible

@app.post("/users")
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db)):
    user = User(email=data.email, name=data.name)
    db.add(user)
    await db.commit()
    return user
```

**Strategy B: Auto-commit on success, rollback on exception**

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

Strategy A is more explicit and gives you finer control. Strategy B is convenient but hides when the commit happens.

---

## 7. Alembic Migrations

### Setup

```bash
pip install alembic
alembic init -t async alembic
```

The `-t async` flag generates an async-compatible `env.py`.

### Configure `alembic.ini`

```ini
# alembic.ini
sqlalchemy.url = postgresql+asyncpg://user:pass@localhost:5432/mydb
```

Better: load the URL from your app config (see `env.py` below).

### Configure `env.py`

```python
# alembic/env.py
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Import your models' Base so Alembic can see the metadata
from app.models import Base
from app.database import DATABASE_URL

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
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
    engine = create_async_engine(DATABASE_URL)
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

### Common Commands

```bash
# Create a migration from model changes
alembic revision --autogenerate -m "add users table"

# Apply all pending migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# See current migration state
alembic current

# See migration history
alembic history --verbose

# Generate a blank migration (for custom SQL)
alembic revision -m "add custom index"
```

### Autogenerate Limitations

Alembic autogenerate detects:

| Detected | Not Detected |
|----------|-------------|
| Table additions/removals | Table renames |
| Column additions/removals | Column renames |
| Nullable changes | Enum value changes |
| Index and unique constraint changes | Changes to CHECK constraints |
| Foreign key changes | Custom SQL functions or triggers |

**Always review generated migrations before running them.** Autogenerate is a starting point, not a finished product.

### Example Migration

```python
"""add users table

Revision ID: a1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
```

---

## 8. Common Patterns

### Repository Pattern

Encapsulate all database access behind a class. This keeps your endpoints clean and your queries testable.

```python
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, email: str, name: str) -> User:
        user = User(email=email, name=name)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        is_active: bool | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[User], int]:
        stmt = select(User)
        count_stmt = select(func.count()).select_from(User)

        if is_active is not None:
            stmt = stmt.where(User.is_active == is_active)
            count_stmt = count_stmt.where(User.is_active == is_active)

        stmt = stmt.offset(offset).limit(limit).order_by(User.id)

        result = await self.session.execute(stmt)
        total = await self.session.execute(count_stmt)

        return list(result.scalars().all()), total.scalar_one()

    async def get_with_posts(self, user_id: int) -> User | None:
        stmt = (
            select(User)
            .options(selectinload(User.posts))
            .where(User.id == user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
```

**Wiring it into FastAPI:**

```python
def get_user_repo(db: AsyncSession = Depends(get_db)) -> UserRepository:
    return UserRepository(db)


@app.get("/users/{user_id}")
async def get_user(user_id: int, repo: UserRepository = Depends(get_user_repo)):
    user = await repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
```

### Bulk Operations

**Bulk insert with `add_all`:**

```python
async def bulk_create_users(db: AsyncSession, users_data: list[dict]) -> list[User]:
    users = [User(**data) for data in users_data]
    db.add_all(users)
    await db.commit()
    for user in users:
        await db.refresh(user)
    return users
```

**High-performance bulk insert — skip the ORM for thousands of rows:**

```python
from sqlalchemy import insert

async def bulk_insert_users(db: AsyncSession, users_data: list[dict]) -> None:
    stmt = insert(User).values(users_data)
    await db.execute(stmt)
    await db.commit()
```

**`insert().values()` is dramatically faster** for large datasets because it bypasses ORM overhead (identity map tracking, event hooks, etc.).

| Method | 10k rows (approx.) | Use case |
|--------|-------------------|----------|
| `add()` in a loop | ~5-10s | Need ORM events and relationships |
| `add_all()` | ~3-5s | Batch of ORM objects |
| `insert().values()` | ~0.1-0.3s | Raw speed, no ORM features needed |

### Eager vs Lazy Loading in Async

Async SQLAlchemy does not support implicit lazy loading. You have three strategies:

**1. `selectinload` — separate SELECT with IN clause (recommended default)**

```python
stmt = select(User).options(selectinload(User.posts))
```

Produces:

```sql
SELECT * FROM users WHERE ...;
SELECT * FROM posts WHERE author_id IN (1, 2, 3, ...);
```

Good for: one-to-many, many-to-many. Predictable query count: always 2 queries.

**2. `joinedload` — single query with JOIN**

```python
stmt = select(User).options(joinedload(User.posts))
```

Produces:

```sql
SELECT * FROM users JOIN posts ON posts.author_id = users.id WHERE ...;
```

Good for: many-to-one, one-to-one. Be careful with one-to-many — the JOIN multiplies rows and can return duplicates.

**3. `subqueryload` — separate SELECT with subquery**

```python
stmt = select(User).options(subqueryload(User.posts))
```

Good for: complex filters on the parent query. Rarely needed — prefer `selectinload`.

**Comparison:**

| Strategy | Queries | Best for | Pitfall |
|----------|---------|----------|---------|
| `selectinload` | 2 | One-to-many, many-to-many | Large IN clause with many IDs |
| `joinedload` | 1 | Many-to-one, one-to-one | Row multiplication on collections |
| `subqueryload` | 2 | Complex parent filters | Slower than selectinload in most cases |
| `lazy="raise"` | 0 | Preventing accidental loads | Must always specify eager strategy |

---

## 9. Connection Pooling

### How the Pool Works

```
Request arrives
  → session = async_session()
  → session executes a statement
  → pool checks out a connection
  → SQL runs
  → session.close() / context manager exit
  → connection returned to pool
```

Connections are not tied to sessions until a statement is actually executed.

### Configuration

```python
engine = create_async_engine(
    DATABASE_URL,
    pool_size=5,          # steady-state connections
    max_overflow=10,      # burst connections (total max = pool_size + max_overflow)
    pool_timeout=30,      # seconds to wait for a connection before raising
    pool_pre_ping=True,   # test connection liveness before checkout
    pool_recycle=3600,    # recreate connections after N seconds
)
```

### What Each Setting Does

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `pool_size` | 5 | Persistent connections held open |
| `max_overflow` | 10 | Extra connections created under load |
| `pool_timeout` | 30 | Max wait (seconds) when pool is exhausted |
| `pool_pre_ping` | False | Sends a test query before using a connection |
| `pool_recycle` | -1 | Recreate connections after N seconds (-1 = never) |

### Sizing the Pool

**Key formula:**

```
total_max_connections = pool_size + max_overflow
```

This must be less than your database's `max_connections` divided by the number of app instances.

```
Database max_connections = 100
App instances (pods)     = 4
Per-instance budget      = 100 / 4 = 25

pool_size    = 5    (steady state)
max_overflow = 15   (burst, total = 20, leaves headroom)
```

### Production Configurations

**Low-traffic service:**

```python
create_async_engine(
    url,
    pool_size=3,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=1800,
)
```

**Medium-traffic API:**

```python
create_async_engine(
    url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)
```

**High-traffic service:**

```python
create_async_engine(
    url,
    pool_size=20,
    max_overflow=30,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_timeout=10,  # fail fast under extreme load
)
```

### `pool_pre_ping` — Why You Want It

Without `pool_pre_ping`, a connection that was silently closed by the database (idle timeout, server restart, network blip) will cause your next query to fail.

With `pool_pre_ping=True`, SQLAlchemy sends a lightweight `SELECT 1` before each checkout. If the connection is dead, it discards it and creates a new one.

**Cost**: one extra round-trip per checkout.
**Benefit**: eliminates stale connection errors in production.

Always enable this unless you have a specific reason not to.

### `pool_recycle` — When You Need It

Some databases and proxies (MySQL with `wait_timeout`, PgBouncer, cloud SQL proxies) silently kill connections after a timeout. `pool_recycle` ensures connections are replaced before they hit that limit.

```python
# MySQL default wait_timeout = 28800 (8 hours)
# Set recycle well under that
pool_recycle=3600  # 1 hour
```

---

## 10. Raw asyncpg — When to Skip the ORM

asyncpg is the async PostgreSQL driver that SQLAlchemy uses under the hood. You can also use it **directly**, without SQLAlchemy. This gives you maximum performance at the cost of writing raw SQL.

### When to Use Raw asyncpg vs SQLAlchemy

| Concern | Raw asyncpg | SQLAlchemy async |
|---------|-------------|------------------|
| Performance | Fastest possible — no ORM overhead | ~10-30% slower (identity map, event hooks, type coercion) |
| Query building | Raw SQL strings | Python expressions (`select`, `where`, `join`) |
| Migrations | Manual or separate tool | Alembic integration |
| Relationships | Manual JOINs | `selectinload`, `joinedload` |
| Data mapping | Returns `asyncpg.Record` (tuple-like) | Returns ORM objects with attributes |
| Type safety | None (raw SQL) | Column types validated by ORM |
| Complexity | Low (just SQL) | Higher (sessions, models, identity map) |
| Best for | Bulk operations, read-heavy APIs, performance-critical paths | CRUD apps, complex domain models, teams that prefer Python over SQL |

> **Rule of thumb**: Use SQLAlchemy for your application's main data layer. Drop to raw asyncpg for specific hot paths where ORM overhead is measurable — bulk inserts, reporting queries, or read-only endpoints that serve thousands of requests per second.

### asyncpg Basics

#### Connection Pool

```python
import asyncpg

# Create a pool (do this once at startup)
pool = asyncpg.create_pool(
    dsn="postgresql://user:pass@localhost:5432/mydb",
    min_size=5,      # minimum idle connections
    max_size=20,     # maximum connections
    command_timeout=30,
)
```

#### FastAPI Integration

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await asyncpg.create_pool(
        dsn="postgresql://user:pass@localhost:5432/mydb",
        min_size=5,
        max_size=20,
    )
    yield
    await app.state.db_pool.close()

app = FastAPI(lifespan=lifespan)


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.db_pool
```

#### CRUD Operations

```python
from fastapi import Depends

@app.get("/users/{user_id}")
async def get_user(user_id: int, pool: asyncpg.Pool = Depends(get_pool)):
    row = await pool.fetchrow(
        "SELECT id, email, name FROM users WHERE id = $1", user_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@app.get("/users")
async def list_users(pool: asyncpg.Pool = Depends(get_pool)):
    rows = await pool.fetch(
        "SELECT id, email, name FROM users ORDER BY id LIMIT 100"
    )
    return [dict(row) for row in rows]


@app.post("/users")
async def create_user(data: UserCreate, pool: asyncpg.Pool = Depends(get_pool)):
    row = await pool.fetchrow(
        "INSERT INTO users (email, name) VALUES ($1, $2) RETURNING id, email, name",
        data.email, data.name,
    )
    return dict(row)
```

#### Key Methods

| Method | Returns | Use case |
|--------|---------|----------|
| `pool.fetch(query, *args)` | `list[Record]` | Multiple rows |
| `pool.fetchrow(query, *args)` | `Record \| None` | Single row |
| `pool.fetchval(query, *args)` | Single value | Scalar queries (`SELECT count(*)`) |
| `pool.execute(query, *args)` | Status string (`"INSERT 0 1"`) | INSERT/UPDATE/DELETE without returning data |
| `pool.executemany(query, args)` | None | Batch operations |

#### Parameterized Queries

asyncpg uses `$1`, `$2`, ... for parameters (not `%s` or `?`):

```python
# ❌ WRONG — SQL injection vulnerability
await pool.fetch(f"SELECT * FROM users WHERE email = '{email}'")

# ✅ CORRECT — parameterized query
await pool.fetch("SELECT * FROM users WHERE email = $1", email)
```

#### Transactions

```python
async with pool.acquire() as conn:
    async with conn.transaction():
        await conn.execute(
            "UPDATE accounts SET balance = balance - $1 WHERE id = $2",
            amount, from_id,
        )
        await conn.execute(
            "UPDATE accounts SET balance = balance + $1 WHERE id = $2",
            amount, to_id,
        )
        # auto-commits on exit, rolls back on exception
```

#### Bulk Insert (Where asyncpg Really Shines)

```python
# Insert 10,000 rows — asyncpg uses PostgreSQL's binary COPY protocol
records = [(f"user{i}@example.com", f"User {i}") for i in range(10_000)]

async with pool.acquire() as conn:
    await conn.copy_records_to_table(
        "users",
        records=records,
        columns=["email", "name"],
    )
```

`copy_records_to_table` uses PostgreSQL's binary COPY protocol, which is **dramatically** faster than individual INSERTs:

| Method | 10,000 rows | 100,000 rows |
|--------|-------------|--------------|
| Individual INSERTs | ~5-10s | ~50-100s |
| `executemany` | ~1-2s | ~10-20s |
| `copy_records_to_table` | ~0.05s | ~0.3s |
| SQLAlchemy `insert().values()` | ~0.1-0.3s | ~1-3s |

### Using Both Together

You can use SQLAlchemy for most of your app and drop to raw asyncpg for specific operations:

```python
from sqlalchemy.ext.asyncio import AsyncSession

async def bulk_import(db: AsyncSession, records: list[tuple]):
    """Use raw asyncpg connection for bulk import performance."""
    # Get the underlying asyncpg connection from SQLAlchemy
    raw_conn = await db.connection()
    asyncpg_conn = await raw_conn.get_raw_connection()

    # Use asyncpg's COPY protocol directly
    await asyncpg_conn.copy_records_to_table(
        "events",
        records=records,
        columns=["timestamp", "event_type", "payload"],
    )
    await db.commit()
```

### asyncpg Pool Settings

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `min_size` | 10 | Minimum idle connections |
| `max_size` | 10 | Maximum total connections |
| `max_inactive_connection_lifetime` | 300 | Seconds before idle connections are closed |
| `command_timeout` | None | Default timeout for all queries |

> **Note**: asyncpg manages its own connection pool, separate from SQLAlchemy's pool. If you use both, make sure the combined `max_size` doesn't exceed your database's `max_connections`.

---

## 11. Common Mistakes

### Mistake 1: Lazy Loading in Async

This is the most common async SQLAlchemy error.

```python
# ❌ WRONG — lazy loading triggers implicit I/O
@app.get("/users/{user_id}")
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    return {
        "name": user.name,
        "posts": user.posts,  # MissingGreenlet or DetachedInstanceError
    }
```

```python
# ✅ CORRECT — explicitly load relationships
@app.get("/users/{user_id}")
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(User).options(selectinload(User.posts)).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404)
    return {
        "name": user.name,
        "posts": [{"title": p.title} for p in user.posts],  # already loaded
    }
```

**Prevention**: Set `lazy="raise"` on all relationships. This turns silent failures into immediate, obvious errors during development.

### Mistake 2: Forgetting to Commit

```python
# ❌ WRONG — change is never persisted
@app.put("/users/{user_id}/deactivate")
async def deactivate(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    user.is_active = False
    # forgot await db.commit()
    return {"status": "deactivated"}  # lies — nothing was saved
```

```python
# ✅ CORRECT
@app.put("/users/{user_id}/deactivate")
async def deactivate(user_id: int, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    user.is_active = False
    await db.commit()
    return {"status": "deactivated"}
```

### Mistake 3: Session Scope Leaks

```python
# ❌ WRONG — sharing a session across requests via module-level variable
session = async_session()  # one session for all requests

@app.get("/users")
async def list_users():
    result = await session.execute(select(User))  # concurrent requests collide
    return result.scalars().all()
```

```python
# ✅ CORRECT — one session per request via dependency injection
@app.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User))
    return result.scalars().all()
```

Sessions are **not thread-safe or task-safe**. Two concurrent requests sharing a session will corrupt each other's state. Always use one session per request via the dependency.

### Mistake 4: Using `expire_on_commit=True` with Async

```python
# ❌ WRONG — default expire_on_commit=True
async_session = async_sessionmaker(engine)

async def create_user(db: AsyncSession, email: str, name: str) -> User:
    user = User(email=email, name=name)
    db.add(user)
    await db.commit()
    return user  # user.email, user.name are expired — accessing them raises an error
```

```python
# ✅ CORRECT
async_session = async_sessionmaker(engine, expire_on_commit=False)
```

### Mistake 5: Creating the Engine Per Request

```python
# ❌ WRONG — new engine (and pool!) on every request
async def get_db():
    engine = create_async_engine(DATABASE_URL)  # new pool every time!
    async with async_sessionmaker(engine)() as session:
        yield session
    await engine.dispose()
```

```python
# ✅ CORRECT — module-level engine, created once
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
```

Creating an engine per request means creating a connection pool per request. You lose all the benefits of pooling and leak connections.

### Mistake 6: N+1 Query Problem

```python
# ❌ WRONG — N+1 queries
users = (await db.execute(select(User))).scalars().all()
for user in users:
    posts = (await db.execute(
        select(Post).where(Post.author_id == user.id)
    )).scalars().all()
    # 1 query for users + N queries for posts
```

```python
# ✅ CORRECT — 2 queries total
stmt = select(User).options(selectinload(User.posts))
users = (await db.execute(stmt)).scalars().all()
for user in users:
    # user.posts already loaded — no additional queries
    process(user, user.posts)
```

### Mistake 7: Not Handling `IntegrityError`

```python
# ❌ WRONG — duplicate email crashes with 500
@app.post("/users")
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db)):
    user = User(email=data.email, name=data.name)
    db.add(user)
    await db.commit()  # IntegrityError if email exists → unhandled 500
    return user
```

```python
# ✅ CORRECT — handle constraint violations
from sqlalchemy.exc import IntegrityError

@app.post("/users")
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db)):
    user = User(email=data.email, name=data.name)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered")
    await db.refresh(user)
    return user
```

---

## Quick Reference

### Imports You Will Always Need

```python
from sqlalchemy import select, update, delete, func, String, ForeignKey
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship,
    selectinload, joinedload,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession,
)
```

### Session Operations Cheat Sheet

| Operation | Code | When to use |
|-----------|------|-------------|
| Add object | `session.add(obj)` | Creating new records |
| Add many | `session.add_all([obj1, obj2])` | Batch creates |
| Flush | `await session.flush()` | Need server values mid-transaction |
| Commit | `await session.commit()` | Persist changes to database |
| Rollback | `await session.rollback()` | Undo all uncommitted changes |
| Refresh | `await session.refresh(obj)` | Reload object from database |
| Expire | `session.expire(obj)` | Mark object for reload on next access |
| Get by PK | `await session.get(Model, pk)` | Primary key lookup |
| Execute | `await session.execute(stmt)` | Run any SELECT/INSERT/UPDATE/DELETE |
| Scalar | `result.scalar_one_or_none()` | Get one object or None |
| Scalars | `result.scalars().all()` | Get list of objects |

### The Golden Rules

1. **Set `expire_on_commit=False`** on async session factories.
2. **Set `lazy="raise"`** on all relationships. Load what you need explicitly.
3. **One session per request** via `Depends(get_db)`. Never share sessions.
4. **One engine per application**. Never create engines inside request handlers.
5. **Always enable `pool_pre_ping`** in production.
6. **Always review** autogenerated Alembic migrations before running them.
7. **Handle `IntegrityError`** for any operation that touches unique constraints.
