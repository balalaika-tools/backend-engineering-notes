# SQLAlchemy ORM — Core Concepts and Sync Usage

> **What is an ORM?** An Object-Relational Mapper lets you interact with a database using Python objects instead of raw SQL. SQLAlchemy is Python's most powerful ORM and the de-facto standard for production Python backends.

---

## 1. What Is an ORM?

An ORM maps database tables to Python classes and rows to instances:

```
Database Table          Python Class
─────────────────       ─────────────────────
users table       ←→   class User(Base)
column: id        ←→   id: Mapped[int]
column: email     ←→   email: Mapped[str]
row: (1, a@b.com) ←→   User(id=1, email='a@b.com')
```

Instead of writing:

```python
cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
row = cursor.fetchone()
user = {"id": row[0], "email": row[1], "name": row[2]}
```

You write:

```python
user = await session.get(User, user_id)
print(user.email)
```

### ORM Trade-offs

| Benefit | Cost |
|---------|------|
| Less SQL boilerplate | Learning curve |
| Python type safety | Abstraction can hide bad queries |
| Relationship navigation | N+1 queries if you're not careful |
| Automatic migrations with Alembic | Some PostgreSQL features need raw SQL |
| Testability (mock sessions) | Performance overhead (~10-30%) |
| Portable across databases | Can obscure what SQL runs |

### SQLAlchemy Architecture

SQLAlchemy has two layers:

```
SQLAlchemy ORM           ← High-level: mapped classes, sessions, relationships
      ↓
SQLAlchemy Core          ← Mid-level: Table, select(), insert() — generates SQL
      ↓
Driver (asyncpg/psycopg) ← Low-level: speaks to PostgreSQL
      ↓
PostgreSQL
```

You can use Core without the ORM (handwritten SQL with Python constructs) or bypass Core entirely with raw SQL through the driver. In most apps you use all three layers together.

---

## 2. Installation

```bash
# SQLAlchemy + asyncpg (async, recommended)
pip install sqlalchemy asyncpg

# SQLAlchemy + psycopg3 async
pip install sqlalchemy "psycopg[binary]"

# SQLAlchemy + psycopg2 (sync, legacy)
pip install sqlalchemy psycopg2-binary

# Alembic for migrations
pip install alembic
```

---

## 3. SQLAlchemy 2.0 Declarative Style

SQLAlchemy 2.0 (released 2023) introduced a cleaner, type-annotated style. Always use this for new projects.

### Defining Models

```python
from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Text, ForeignKey, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared base for all models. Holds metadata for Alembic."""
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Relationships
    posts: Mapped[list["Post"]] = relationship(back_populates="author", lazy="raise")
    profile: Mapped["Profile | None"] = relationship(back_populates="user", lazy="raise")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    published: Mapped[bool] = mapped_column(default=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    author: Mapped["User"] = relationship(back_populates="posts", lazy="raise")
    tags: Mapped[list["Tag"]] = relationship(
        secondary="post_tags", back_populates="posts", lazy="raise"
    )


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    bio: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(String(500))

    user: Mapped["User"] = relationship(back_populates="profile", lazy="raise")


# Many-to-many association table
from sqlalchemy import Table, Column

post_tags = Table(
    "post_tags",
    Base.metadata,
    Column("post_id", ForeignKey("posts.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)

    posts: Mapped[list["Post"]] = relationship(
        secondary="post_tags", back_populates="tags", lazy="raise"
    )
```

### Column Annotation Patterns

| Pattern | Column type | Nullable |
|---------|------------|---------|
| `Mapped[int]` | INTEGER NOT NULL | No |
| `Mapped[str]` | TEXT NOT NULL | No |
| `Mapped[int \| None]` | INTEGER NULL | Yes |
| `Mapped[str \| None]` | TEXT NULL | Yes |
| `Mapped[bool]` | BOOLEAN NOT NULL | No |
| `Mapped[datetime]` | TIMESTAMP NOT NULL | No |

### `mapped_column` Options

```python
# Primary key (auto-increment)
id: Mapped[int] = mapped_column(primary_key=True)

# String with max length
email: Mapped[str] = mapped_column(String(255))

# Python-side default (evaluated when Python creates the object)
is_active: Mapped[bool] = mapped_column(default=True)

# Server-side default (SQL expression, evaluated when row is inserted)
created_at: Mapped[datetime] = mapped_column(server_default=func.now())

# Unique constraint
email: Mapped[str] = mapped_column(unique=True)

# Index
email: Mapped[str] = mapped_column(index=True)

# Both
email: Mapped[str] = mapped_column(unique=True, index=True)

# Foreign key
author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

# Custom column name in DB
first_name: Mapped[str] = mapped_column("first_name", String(50))

# Not mapped to a column (just a Python attribute)
# Use @property instead

# Check constraint
from sqlalchemy import CheckConstraint
__table_args__ = (CheckConstraint("age >= 0", name="ck_users_age"),)
```

### Advanced Column Types

```python
import uuid
from sqlalchemy import UUID, JSONB, ARRAY, Integer

# UUID primary key
id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
)

# JSONB
metadata: Mapped[dict] = mapped_column(JSONB, default=dict)

# Array
tags: Mapped[list[str]] = mapped_column(ARRAY(String))

# Numeric (exact decimal for money)
price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
```

### Table-Level Constraints and Indexes

```python
from sqlalchemy import UniqueConstraint, Index

class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        # Composite unique constraint
        UniqueConstraint("user_id", "team_id", name="uq_membership_user_team"),
        # Partial index
        Index("ix_active_memberships", "user_id", postgresql_where="is_active = TRUE"),
        # Multi-column index
        Index("ix_posts_author_created", "author_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    is_active: Mapped[bool] = mapped_column(default=True)
```

---

## 4. Relationship Types

### One-to-Many

One user has many posts:

```python
class User(Base):
    posts: Mapped[list["Post"]] = relationship(back_populates="author", lazy="raise")

class Post(Base):
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    author: Mapped["User"] = relationship(back_populates="posts", lazy="raise")
```

### One-to-One

One user has one profile:

```python
class User(Base):
    profile: Mapped["Profile | None"] = relationship(
        back_populates="user", lazy="raise", uselist=False
    )

class Profile(Base):
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    user: Mapped["User"] = relationship(back_populates="profile", lazy="raise")
```

### Many-to-Many

Posts have multiple tags; tags belong to multiple posts:

```python
post_tags = Table(
    "post_tags",
    Base.metadata,
    Column("post_id", ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

class Post(Base):
    tags: Mapped[list["Tag"]] = relationship(
        secondary="post_tags", back_populates="posts", lazy="raise"
    )

class Tag(Base):
    posts: Mapped[list["Post"]] = relationship(
        secondary="post_tags", back_populates="tags", lazy="raise"
    )
```

### `lazy` Options

| Value | Behavior | Use in async |
|-------|----------|-------------|
| `"select"` | Lazy load on attribute access | ❌ Raises MissingGreenlet |
| `"raise"` | Raises error on attribute access | ✅ Forces explicit loading |
| `"raise_on_sql"` | Raises only if SQL would be issued | ✅ Safe |
| `"selectin"` | Always loads via SELECT IN | ✅ Auto-eager (use carefully) |
| `"joined"` | Always loads via JOIN | ✅ Auto-eager (use carefully) |
| `"subquery"` | Loads via subquery | ✅ Auto-eager |
| `"dynamic"` | Returns query (deprecated 2.0) | ❌ Don't use |
| `"write_only"` | Write-only relationship | ✅ For large collections |

**For async: always use `lazy="raise"`** as the default. Load explicitly with `selectinload()` or `joinedload()` when needed.

### Cascade Options

```python
# Delete posts when user is deleted
posts: Mapped[list["Post"]] = relationship(
    back_populates="author",
    cascade="all, delete-orphan",  # Python-side cascade
    lazy="raise",
)

# Or at the DB level (more reliable)
author_id: Mapped[int] = mapped_column(
    ForeignKey("users.id", ondelete="CASCADE")
)
```

`cascade="all, delete-orphan"` means: when you delete a User, SQLAlchemy will also delete all their Posts (via Python). Combined with `ON DELETE CASCADE` at the DB level, you get double protection.

---

## 5. Sync SQLAlchemy — Engine and Session

For scripts, background jobs, or services that don't need async:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

DATABASE_URL = "postgresql://user:pass@localhost:5432/mydb"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,  # True logs SQL
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=True,  # sync: True is fine (lazy reloads work)
)
```

### Using Sync Sessions

```python
# Context manager (always use this)
with SessionLocal() as session:
    user = session.get(User, 1)
    print(user.email)

# Or manually
session = SessionLocal()
try:
    user = session.get(User, 1)
    session.commit()
finally:
    session.close()
```

### Sync CRUD

```python
from sqlalchemy import select

# Create
def create_user(session: Session, email: str, name: str) -> User:
    user = User(email=email, name=name)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user

# Read by primary key (checks identity map first)
def get_user(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)

# Read with filter
def get_user_by_email(session: Session, email: str) -> User | None:
    stmt = select(User).where(User.email == email)
    return session.execute(stmt).scalar_one_or_none()

# Read many
def list_active_users(session: Session, limit: int = 20) -> list[User]:
    stmt = select(User).where(User.is_active == True).limit(limit).order_by(User.id)
    return list(session.execute(stmt).scalars().all())

# Update
def deactivate_user(session: Session, user_id: int) -> bool:
    user = session.get(User, user_id)
    if not user:
        return False
    user.is_active = False
    session.commit()
    return True

# Delete
def delete_user(session: Session, user_id: int) -> bool:
    user = session.get(User, user_id)
    if not user:
        return False
    session.delete(user)
    session.commit()
    return True
```

### Sync with Relationships

```python
from sqlalchemy.orm import selectinload, joinedload

# selectinload (2 queries, recommended for collections)
def get_user_with_posts(session: Session, user_id: int) -> User | None:
    stmt = (
        select(User)
        .options(selectinload(User.posts))
        .where(User.id == user_id)
    )
    return session.execute(stmt).scalar_one_or_none()

# joinedload (1 query with JOIN, for many-to-one / one-to-one)
def get_post_with_author(session: Session, post_id: int) -> Post | None:
    stmt = (
        select(Post)
        .options(joinedload(Post.author))
        .where(Post.id == post_id)
    )
    return session.execute(stmt).scalar_one_or_none()
```

---

## 6. SQLAlchemy Core — Queries Without the ORM

SQLAlchemy Core lets you build SQL queries in Python without full ORM mapping. Useful when you want SQL control but still want Python's query building:

```python
from sqlalchemy import select, insert, update, delete, text, func

# SELECT
stmt = (
    select(User.id, User.email, func.count(Post.id).label("post_count"))
    .join(Post, Post.author_id == User.id, isouter=True)
    .where(User.is_active == True)
    .group_by(User.id, User.email)
    .order_by(func.count(Post.id).desc())
    .limit(10)
)
result = session.execute(stmt)
rows = result.all()  # list of Row objects (not ORM instances)

# Raw SQL with text()
stmt = text("SELECT id, email FROM users WHERE email = :email")
result = session.execute(stmt, {"email": "alice@example.com"})
row = result.first()

# INSERT with Core
stmt = insert(User).values(email="alice@example.com", name="Alice").returning(User.id)
result = session.execute(stmt)
user_id = result.scalar_one()
session.commit()

# UPDATE with Core
stmt = update(User).where(User.id == 1).values(name="Alice Smith").returning(User.id)
result = session.execute(stmt)
session.commit()

# DELETE with Core
stmt = delete(User).where(User.is_active == False).returning(User.id)
result = session.execute(stmt)
deleted_ids = result.scalars().all()
session.commit()
```

---

## 7. Alembic — Database Migrations

Alembic manages schema changes over time. It generates Python migration scripts that you can apply, roll back, and version control.

### Setup

```bash
alembic init -t async alembic  # async template
# or
alembic init alembic           # sync template
```

This creates:

```
alembic/
    env.py          ← configure DB connection and metadata
    script.py.mako  ← migration file template
    versions/       ← generated migration files
alembic.ini         ← alembic configuration
```

### Configure `alembic.ini`

```ini
# alembic.ini
# Leave sqlalchemy.url empty — we'll set it in env.py from app config
sqlalchemy.url =
```

### Configure `env.py` (Sync)

```python
# alembic/env.py
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool

from app.models import Base  # import all models so Alembic sees the metadata
from app.config import settings

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### Configure `env.py` (Async)

```python
# alembic/env.py — async version
import asyncio
from logging.config import fileConfig
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import Base
from app.config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
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

### Key Alembic Commands

```bash
# Generate a migration from model changes (autogenerate)
alembic revision --autogenerate -m "add users and posts tables"

# Apply all pending migrations
alembic upgrade head

# Apply one migration forward
alembic upgrade +1

# Roll back one migration
alembic downgrade -1

# Roll back to a specific revision
alembic downgrade a1b2c3d4

# Roll back everything
alembic downgrade base

# See current state
alembic current

# See migration history
alembic history --verbose

# Generate empty migration (for custom SQL)
alembic revision -m "add full text search index"

# Show the SQL Alembic would run, without running it
alembic upgrade head --sql
```

### What a Migration Looks Like

```python
"""add users and posts tables

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2024-01-15
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = None  # None = first migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_posts_author_id", "posts", ["author_id"])


def downgrade() -> None:
    op.drop_index("ix_posts_author_id")
    op.drop_table("posts")
    op.drop_index("ix_users_email")
    op.drop_table("users")
```

### Adding a Column to an Existing Table

```python
"""add last_login to users

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "last_login",
        sa.TIMESTAMP(timezone=True),
        nullable=True,  # nullable so existing rows don't violate NOT NULL
    ))


def downgrade() -> None:
    op.drop_column("users", "last_login")
```

### Autogenerate Limitations

Alembic detects:

| Detected | Not Detected |
|----------|-------------|
| Table additions/removals | Table renames |
| Column additions/removals | Column renames |
| Nullable changes | Enum value additions |
| Index changes | Custom SQL functions/triggers |
| FK changes | Changes to CHECK constraints |
| Unique constraint changes | Sequence changes |

**Always review autogenerated migrations before running.** They are a starting point, not a finished product.

### Production Migration Strategy

```bash
# 1. Review the generated SQL before running in production
alembic upgrade head --sql > migration_preview.sql

# 2. Run with a transaction (Alembic wraps each migration in a transaction by default)
alembic upgrade head

# 3. For large tables: add columns as nullable first, backfill, then add NOT NULL
# Step 1: nullable column (fast, no table rewrite)
op.add_column("users", sa.Column("tier", sa.String(20), nullable=True))
# Step 2: backfill (can run concurrently in production)
op.execute("UPDATE users SET tier = 'free' WHERE tier IS NULL")
# Step 3: add NOT NULL constraint
op.alter_column("users", "tier", nullable=False)
```

---

## 8. Project Structure

```
app/
├── __init__.py
├── main.py              # FastAPI app, lifespan, router includes
├── config.py            # settings (from env vars)
├── database.py          # engine, session factory, get_db
├── models/
│   ├── __init__.py      # imports all models (so Alembic sees them)
│   ├── base.py          # DeclarativeBase
│   ├── user.py
│   ├── post.py
│   └── tag.py
├── schemas/             # Pydantic models (request/response)
│   ├── user.py
│   └── post.py
├── repositories/        # database access layer
│   ├── user_repo.py
│   └── post_repo.py
├── routers/
│   ├── users.py
│   └── posts.py
alembic/
├── env.py
├── versions/
│   └── a1b2c3_initial.py
alembic.ini
```

**`database.py`:**

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from typing import AsyncGenerator
from app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
```

**`models/__init__.py`** — critical for Alembic:

```python
# Import all models here so Alembic's target_metadata sees them all
from app.models.base import Base
from app.models.user import User
from app.models.post import Post
from app.models.tag import Tag

__all__ = ["Base", "User", "Post", "Tag"]
```

If a model isn't imported before Alembic reads `Base.metadata`, it won't generate migrations for that table. This `__init__.py` is the one place you guarantee all models are imported.

---

## 9. Common ORM Pitfalls

### The N+1 Problem

```python
# ❌ N+1 — fetches all users, then 1 query per user for posts
users = session.execute(select(User)).scalars().all()
for user in users:
    print(user.posts)  # 1 query here × N users

# ✅ 2 queries total
users = session.execute(
    select(User).options(selectinload(User.posts))
).scalars().all()
for user in users:
    print(user.posts)  # already loaded
```

### Identity Map Confusion

SQLAlchemy's session maintains an **identity map** — one Python object per row (by primary key). If you load the same row twice, you get the same object:

```python
user1 = session.get(User, 1)
user2 = session.get(User, 1)
assert user1 is user2  # True — same object
```

This is efficient (no redundant queries) but can cause confusion if you expect two independent copies.

### Detached Instance Error

Accessing a lazy-loaded attribute after the session is closed:

```python
# ❌ Session closed before accessing relationship
with SessionLocal() as session:
    user = session.get(User, 1)

print(user.posts)  # DetachedInstanceError — session is closed, can't load

# ✅ Load everything you need within the session
with SessionLocal() as session:
    user = session.execute(
        select(User).options(selectinload(User.posts)).where(User.id == 1)
    ).scalar_one_or_none()

print(user.posts)  # works — already loaded
```

### SQLite Differences to Watch For

If you develop with SQLite and deploy with PostgreSQL:

```python
# SQLite doesn't enforce foreign keys by default
# Enable it explicitly for tests
from sqlalchemy import event

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
```
