# Python Database Drivers — psycopg3 and asyncpg

> **What is a driver?** A database driver is the low-level library that speaks the database wire protocol. SQLAlchemy and other ORMs sit on top of a driver — they don't communicate with PostgreSQL themselves, they delegate to psycopg3 or asyncpg.

```
Your code (FastAPI endpoint)
    ↓
ORM (SQLAlchemy)          ← optional layer
    ↓
Driver (psycopg3 / asyncpg)
    ↓
PostgreSQL (wire protocol)
```

You'll usually use a driver indirectly through SQLAlchemy. But understanding drivers directly helps you:
- Write raw SQL for performance-critical paths
- Understand what SQLAlchemy does under the hood
- Debug connection issues
- Use drivers in scripts or tools where an ORM is overkill

---

## 1. psycopg3 (psycopg)

psycopg3 is the third major version of the most widely used Python PostgreSQL driver. It supports both synchronous and async usage.

### Install

```bash
pip install psycopg[binary]       # recommended — includes compiled C extensions
pip install psycopg               # pure Python fallback
pip install psycopg[binary,pool]  # with built-in connection pool
```

### 1.1 Synchronous psycopg3

Use sync psycopg3 for scripts, CLIs, background jobs, or anywhere you're not in an async framework.

#### Connecting and Executing

```python
import psycopg

# Single connection — use as context manager (auto-closes)
with psycopg.connect("postgresql://user:pass@localhost:5432/mydb") as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, email, name FROM users WHERE id = %s", (1,))
        row = cur.fetchone()
        print(row)  # (1, 'alice@example.com', 'Alice')
```

#### Parameters

psycopg3 uses `%s` as the placeholder for all types (not `%d`, `%f`, etc.):

```python
# ✅ Parameterized — safe
cur.execute("SELECT * FROM users WHERE email = %s AND is_active = %s", ("alice@example.com", True))

# ❌ NEVER do this — SQL injection
cur.execute(f"SELECT * FROM users WHERE email = '{email}'")
```

Named parameters:

```python
cur.execute(
    "SELECT * FROM users WHERE email = %(email)s",
    {"email": "alice@example.com"},
)
```

#### Fetching Results

```python
with conn.cursor() as cur:
    cur.execute("SELECT id, email, name FROM users LIMIT 10")

    # All rows as list of tuples
    rows = cur.fetchall()       # [(1, 'a@b.com', 'Alice'), ...]

    # One row (advances cursor)
    row = cur.fetchone()        # (1, 'a@b.com', 'Alice') or None

    # N rows
    rows = cur.fetchmany(5)     # up to 5 rows
```

#### Dictionary Rows (Row Factories)

By default you get tuples. Use a row factory for named access:

```python
from psycopg.rows import dict_row, namedtuple_row, class_row

# Dict rows
with psycopg.connect(connstr, row_factory=dict_row) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, email FROM users")
        row = cur.fetchone()
        print(row["email"])  # 'alice@example.com'

# Named tuple rows
with psycopg.connect(connstr, row_factory=namedtuple_row) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, email FROM users")
        row = cur.fetchone()
        print(row.email)  # 'alice@example.com'

# Per-cursor override
with conn.cursor(row_factory=dict_row) as cur:
    ...
```

#### Transactions

psycopg3 is **autocommit=False by default** — every operation is in a transaction.

```python
# Commit explicitly
with psycopg.connect(connstr) as conn:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (email, name) VALUES (%s, %s)", ("a@b.com", "Alice"))
        cur.execute("INSERT INTO users (email, name) VALUES (%s, %s)", ("x@y.com", "Bob"))
    conn.commit()
```

With context managers, `conn.commit()` is called automatically on clean exit, `conn.rollback()` on exception:

```python
with psycopg.connect(connstr) as conn:
    conn.execute("INSERT INTO users (email, name) VALUES (%s, %s)", ("a@b.com", "Alice"))
    # conn.__exit__ calls commit() on success, rollback() on exception
```

Explicit rollback and savepoints:

```python
with psycopg.connect(connstr) as conn:
    try:
        conn.execute("UPDATE accounts SET balance = balance - 100 WHERE id = 1")
        conn.execute("UPDATE accounts SET balance = balance + 100 WHERE id = 2")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
```

Autocommit mode (each statement is its own transaction):

```python
with psycopg.connect(connstr, autocommit=True) as conn:
    conn.execute("VACUUM")  # requires autocommit — cannot run inside a transaction
```

#### RETURNING Clause

```python
with conn.cursor() as cur:
    cur.execute(
        "INSERT INTO users (email, name) VALUES (%s, %s) RETURNING id, created_at",
        ("alice@example.com", "Alice"),
    )
    row = cur.fetchone()
    print(row)  # (42, datetime(2024, ...))
```

#### Bulk Insert with `executemany` and COPY

```python
# executemany — one statement, multiple parameter sets
users_data = [("a@b.com", "Alice"), ("x@y.com", "Bob"), ("q@r.com", "Carol")]

with conn.cursor() as cur:
    cur.executemany(
        "INSERT INTO users (email, name) VALUES (%s, %s)",
        users_data,
    )
conn.commit()
```

For maximum performance, use `COPY`:

```python
with conn.cursor() as cur:
    with cur.copy("COPY users (email, name) FROM STDIN") as copy:
        for email, name in users_data:
            copy.write_row((email, name))
conn.commit()
```

`COPY` is 10-100x faster than `executemany` for large datasets.

#### Connection Pool (sync)

```python
from psycopg_pool import ConnectionPool

pool = ConnectionPool(
    conninfo="postgresql://user:pass@localhost:5432/mydb",
    min_size=2,
    max_size=10,
)

# Use the pool
with pool.connection() as conn:
    result = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    print(result[0])

# Close when done
pool.close()
```

---

### 1.2 Async psycopg3

Same API, but with `await`. Use in FastAPI, asyncio scripts, or anywhere you're in an async context.

```python
import psycopg

# Single async connection
async with await psycopg.AsyncConnection.connect(connstr) as conn:
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, email FROM users WHERE id = %s", (1,))
        row = await cur.fetchone()
        print(row)
```

#### Async Pool with FastAPI

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from psycopg_pool import AsyncConnectionPool

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = AsyncConnectionPool(
        conninfo="postgresql://user:pass@localhost:5432/mydb",
        min_size=2,
        max_size=10,
        open=False,  # don't block during __init__
    )
    await app.state.db_pool.open()
    yield
    await app.state.db_pool.close()

app = FastAPI(lifespan=lifespan)


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.db_pool
```

#### CRUD with Async psycopg3

```python
from fastapi import Depends, HTTPException
from psycopg.rows import dict_row

@app.get("/users/{user_id}")
async def get_user(user_id: int, pool: AsyncConnectionPool = Depends(get_pool)):
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, email, name FROM users WHERE id = %s", (user_id,)
            )
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return row


@app.post("/users")
async def create_user(data: UserCreate, pool: AsyncConnectionPool = Depends(get_pool)):
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO users (email, name) VALUES (%s, %s) RETURNING id, email, name",
                (data.email, data.name),
            )
            row = await cur.fetchone()
    return row


@app.get("/users")
async def list_users(
    limit: int = 20,
    offset: int = 0,
    pool: AsyncConnectionPool = Depends(get_pool),
):
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, email, name FROM users ORDER BY id LIMIT %s OFFSET %s",
                (limit, offset),
            )
            rows = await cur.fetchall()
    return rows
```

#### Async Transactions

```python
async with pool.connection() as conn:
    async with conn.transaction():
        await conn.execute(
            "UPDATE accounts SET balance = balance - %s WHERE id = %s",
            (amount, from_id),
        )
        await conn.execute(
            "UPDATE accounts SET balance = balance + %s WHERE id = %s",
            (amount, to_id),
        )
        # auto-commits on exit, rolls back on exception
```

#### Async COPY (Bulk Insert)

```python
async with pool.connection() as conn:
    async with conn.cursor() as cur:
        async with cur.copy("COPY users (email, name) FROM STDIN") as copy:
            for email, name in users_data:
                await copy.write_row((email, name))
```

---

## 2. asyncpg

asyncpg is a high-performance, pure-async PostgreSQL driver written in Cython. It's faster than psycopg3 async for most workloads because it uses PostgreSQL's binary protocol for all communication and avoids Python overhead.

### Install

```bash
pip install asyncpg
```

### 2.1 Connection and Pool

```python
import asyncpg

# Single connection (for scripts)
conn = await asyncpg.connect("postgresql://user:pass@localhost:5432/mydb")
await conn.close()

# Connection pool (for applications)
pool = await asyncpg.create_pool(
    dsn="postgresql://user:pass@localhost:5432/mydb",
    min_size=5,      # connections always open
    max_size=20,     # max connections
    command_timeout=30,
)
await pool.close()
```

### 2.2 Parameters

asyncpg uses `$1`, `$2`, ... (PostgreSQL's native parameter format):

```python
# ✅ Parameterized
row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", "alice@example.com")

# ✅ Multiple parameters
row = await conn.fetchrow(
    "SELECT * FROM users WHERE email = $1 AND is_active = $2",
    "alice@example.com", True,
)

# ❌ NEVER — SQL injection
row = await conn.fetchrow(f"SELECT * FROM users WHERE email = '{email}'")
```

### 2.3 Fetch Methods

| Method | Returns | Use case |
|--------|---------|----------|
| `fetch(query, *args)` | `list[Record]` | Multiple rows |
| `fetchrow(query, *args)` | `Record \| None` | Single row |
| `fetchval(query, *args, column=0)` | Single value | Scalar (`SELECT COUNT(*)`) |
| `execute(query, *args)` | Status string (`"INSERT 0 1"`) | INSERT/UPDATE/DELETE |
| `executemany(query, args_list)` | None | Batch operations |

```python
# Multiple rows
users = await pool.fetch("SELECT id, email FROM users ORDER BY id LIMIT 100")
for user in users:
    print(user["id"], user["email"])  # asyncpg.Record supports attribute access

# Single row
user = await pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
if user is None:
    raise HTTPException(status_code=404)
return dict(user)

# Scalar value
count = await pool.fetchval("SELECT COUNT(*) FROM users WHERE is_active = $1", True)

# Execute (no return data needed)
status = await pool.execute(
    "UPDATE users SET is_active = $1 WHERE id = $2", False, user_id
)
# status = "UPDATE 1"
```

### 2.4 asyncpg.Record

`asyncpg.Record` is tuple-like and dict-like:

```python
row = await pool.fetchrow("SELECT id, email, name FROM users WHERE id = $1", 1)

# Access by column name
print(row["email"])         # 'alice@example.com'

# Access by index
print(row[1])               # 'alice@example.com'

# Convert to dict
user_dict = dict(row)       # {"id": 1, "email": "...", "name": "..."}

# Check if None
if row is None: ...         # no row found
```

### 2.5 FastAPI Integration

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException
import asyncpg

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


@app.get("/users/{user_id}")
async def get_user(user_id: int, pool: asyncpg.Pool = Depends(get_pool)):
    row = await pool.fetchrow(
        "SELECT id, email, name FROM users WHERE id = $1", user_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(row)


@app.post("/users", status_code=201)
async def create_user(data: UserCreate, pool: asyncpg.Pool = Depends(get_pool)):
    try:
        row = await pool.fetchrow(
            "INSERT INTO users (email, name) VALUES ($1, $2) RETURNING id, email, name",
            data.email, data.name,
        )
        return dict(row)
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="Email already registered")
```

### 2.6 Transactions

```python
# Simple transaction via acquire
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
        # commits on exit, rolls back on exception

# Nested transaction (savepoint)
async with pool.acquire() as conn:
    async with conn.transaction():
        # outer transaction
        await conn.execute("INSERT INTO logs (msg) VALUES ($1)", "start")

        async with conn.transaction():
            # nested — creates a SAVEPOINT
            try:
                await conn.execute("INSERT INTO users (email) VALUES ($1)", duplicate)
            except asyncpg.UniqueViolationError:
                pass  # savepoint rolled back, outer transaction continues

        await conn.execute("INSERT INTO logs (msg) VALUES ($1)", "done")
```

### 2.7 Bulk Insert — Where asyncpg Excels

asyncpg exposes PostgreSQL's binary `COPY` protocol directly:

```python
# copy_records_to_table — fastest possible bulk insert
records = [(f"user{i}@example.com", f"User {i}") for i in range(100_000)]

async with pool.acquire() as conn:
    await conn.copy_records_to_table(
        "users",
        records=records,
        columns=["email", "name"],
    )
```

Performance comparison for 100,000 rows:

| Method | Time (approx) |
|--------|--------------|
| Individual `await pool.execute(INSERT ...)` in loop | ~100s |
| `executemany` | ~10-20s |
| `copy_records_to_table` | ~0.3s |

### 2.8 asyncpg-Specific Features

#### Prepared Statements

asyncpg automatically prepares statements on first use. You can also do it explicitly:

```python
# Explicit prepared statement — useful for queries run thousands of times
stmt = await conn.prepare("SELECT * FROM users WHERE id = $1")
for user_id in user_ids:
    row = await stmt.fetchrow(user_id)
```

#### Type Codecs

asyncpg understands PostgreSQL types natively. UUIDs come back as `uuid.UUID`, timestamps as `datetime`, etc.

```python
# Custom codec for a type you define
await conn.set_type_codec(
    "jsonb",
    encoder=json.dumps,
    decoder=json.loads,
    schema="pg_catalog",
)
```

#### Listeners (LISTEN/NOTIFY)

```python
# Subscribe to a PostgreSQL channel
await conn.add_listener("events_channel", callback)

# In another connection
await conn.execute("NOTIFY events_channel, 'user_created:42'")
```

### 2.9 Error Handling

asyncpg has typed exceptions for PostgreSQL error codes:

```python
import asyncpg

try:
    await pool.execute("INSERT INTO users (email) VALUES ($1)", email)
except asyncpg.UniqueViolationError:
    raise HTTPException(409, "Email already exists")
except asyncpg.ForeignKeyViolationError:
    raise HTTPException(400, "Referenced record does not exist")
except asyncpg.NotNullViolationError:
    raise HTTPException(400, "Required field missing")
except asyncpg.PostgresError as e:
    # Catch any other PostgreSQL error
    logger.error("Database error", code=e.sqlstate, msg=str(e))
    raise HTTPException(500, "Database error")
```

---

## 3. psycopg3 vs asyncpg — Comparison

| Feature | psycopg3 | asyncpg |
|---------|----------|---------|
| Sync support | Yes (native) | No |
| Async support | Yes | Yes (native, optimized) |
| Performance (async) | Good | Excellent (binary protocol) |
| Parameter style | `%s` (standard DB-API) | `$1`, `$2`, ... (PostgreSQL native) |
| Row type | tuple / dict / namedtuple (configurable) | `asyncpg.Record` (tuple+dict-like) |
| COPY support | Yes | Yes (binary, fastest) |
| SQLAlchemy backend | `psycopg` (recommended for new projects) | `asyncpg` |
| Typed exceptions | Via `pgcode` | Rich exception hierarchy |
| Connection pool | `psycopg_pool` (separate package) | Built-in |
| Python version | 3.8+ | 3.8+ |
| Type codecs | Good | Extensive, binary-level |

### Which to Choose?

**Use psycopg3 when:**
- You need both sync and async in the same project
- You're using SQLAlchemy with psycopg3 backend (modern, actively maintained)
- You want DB-API 2.0 compatibility (standard Python DB interface)
- You're coming from psycopg2 and want a familiar API

**Use asyncpg when:**
- Pure async application (FastAPI, asyncio)
- Maximum performance is critical (high-throughput APIs, real-time processing)
- You want the simplest possible async code without adapting from sync
- You're doing heavy bulk operations and want binary COPY

**In practice**: asyncpg is slightly faster, psycopg3 is more versatile. Either works well behind SQLAlchemy. For raw driver usage, asyncpg has a cleaner async API.

---

## 4. Raw Drivers vs ORM — When to Use Each

| Situation | Use |
|-----------|-----|
| Rapid CRUD app development | SQLAlchemy ORM |
| Complex domain model with many relationships | SQLAlchemy ORM |
| Team prefers Python over SQL | SQLAlchemy ORM |
| Reporting / analytics queries | Raw SQL via driver |
| Bulk insert (10k+ rows) | asyncpg `copy_records_to_table` |
| Scripting / one-off migrations | psycopg3 or asyncpg directly |
| Extremely performance-sensitive read endpoints | Raw asyncpg |
| You need maximum control over SQL | Raw driver |

> You don't have to choose one. In a SQLAlchemy app, you can drop to raw asyncpg for specific hot paths: `raw_conn = await session.connection(); asyncpg_conn = await raw_conn.get_raw_connection()`.
