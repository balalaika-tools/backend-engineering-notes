# Connection Pooling — Deep Dive

> **Why pooling matters**: Opening a PostgreSQL connection takes ~5-50ms and consumes ~5-10MB of server memory. Without a pool, every request would pay this cost and your database would run out of connections under load.

---

## 1. What Is a Connection Pool?

A connection pool is a cache of pre-opened database connections. When your code needs a connection, it borrows one from the pool. When done, it returns it — the connection stays open for the next request.

```
Without pool:
  Request → open connection (5-50ms) → query → close connection → repeat

With pool:
  App start → open 5 connections → keep them
  Request → borrow connection (0ms) → query → return to pool → repeat
```

The pool is the single most important performance optimization for database-backed applications.

---

## 2. SQLAlchemy's Built-in Pool

SQLAlchemy manages its own connection pool. You configure it when creating the engine.

### Pool Types

| Pool Class | Behavior | Use case |
|-----------|----------|----------|
| `QueuePool` (default) | Fixed size + overflow | Web applications |
| `NullPool` | No pooling — new connection per request | Alembic migrations, serverless |
| `StaticPool` | One shared connection, never closed | SQLite in-memory for testing |
| `AssertionPool` | Raises if more than one connection at a time | Debugging |
| `AsyncAdaptedQueuePool` | Async version of QueuePool | Async SQLAlchemy (default) |

### Parameters

```python
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(
    DATABASE_URL,
    # Pool size
    pool_size=10,          # connections kept open always (steady state)
    max_overflow=20,       # extra connections created under load
                           # total max = pool_size + max_overflow = 30
    # Timeouts
    pool_timeout=30,       # seconds to wait when pool is exhausted (raises TimeoutError)
    # Reliability
    pool_pre_ping=True,    # test connection liveness before checkout
    pool_recycle=3600,     # recycle connections after N seconds (prevents stale connections)
    # Debugging
    echo_pool=False,       # True logs pool events (checkouts, checkins, creates)
)
```

### What Each Setting Does

#### `pool_size`

The number of connections held open at all times. Even with no traffic, the pool keeps `pool_size` connections open.

```
pool_size=5 means:
  App startup → connect 0 connections (lazy)
  First 5 requests arrive → create 5 connections
  Requests complete → 5 connections stay open, idle
  New request arrives → reuse an idle connection instantly
```

#### `max_overflow`

Additional connections created when all `pool_size` connections are checked out.

```
pool_size=5, max_overflow=10:
  Connections 1-5: from pool_size (permanent, never closed unless recycled)
  Connections 6-15: overflow (created on demand, closed when returned)
  Connection 16+: caller blocks until pool_timeout, then TimeoutError
```

#### `pool_timeout`

How long to wait for a connection when the pool is exhausted. Under extreme load:

```python
# Low timeout — fail fast rather than queue
pool_timeout=5   # good for user-facing APIs (better to 503 than wait 30s)

# High timeout — queue requests
pool_timeout=60  # for batch jobs that can wait
```

#### `pool_pre_ping`

Before handing a connection to your code, SQLAlchemy sends `SELECT 1`. If the connection is dead (server restart, network drop, cloud proxy killed it), it creates a fresh one.

```
Without pre_ping:
  Connection goes stale after idle → next query fails with "connection lost"

With pre_ping:
  Stale connection detected → discarded → fresh connection used → query succeeds
```

Cost: one extra round-trip per checkout (~0.1ms). Worth it for any production deployment.

#### `pool_recycle`

Connections are forcibly closed and recreated after this many seconds. Needed for:
- Cloud SQL proxies that kill long-lived connections
- PgBouncer session mode (if idle connections time out)
- Any database with a `idle_in_transaction_session_timeout`

```python
pool_recycle=1800   # 30 minutes — good default
pool_recycle=3600   # 1 hour
pool_recycle=-1     # never recycle (default — dangerous in some environments)
```

---

## 3. Pool Sizing Math

Getting pool size wrong causes two failure modes:
- **Too small**: requests queue up waiting for connections → high latency
- **Too large**: exhausts database `max_connections` → connections refused

### The Formula

```
max_connections (PostgreSQL) = pool_size + max_overflow
total_connections_used       = per_instance_max × number_of_app_instances

total_connections_used < max_connections × 0.9  (leave 10% headroom)
```

### Step-by-Step Calculation

**Step 1**: Check your PostgreSQL `max_connections`:

```sql
SHOW max_connections;  -- default: 100
```

**Step 2**: Reserve connections for monitoring and admin:

```
Reserved = 5-10 connections (psql access, monitoring agents, etc.)
Available = 100 - 10 = 90
```

**Step 3**: Divide by number of app instances:

```
App instances (pods/workers) = 4
Per-instance budget = 90 / 4 = 22 connections
```

**Step 4**: Set pool_size and max_overflow:

```python
pool_size=5      # steady state (comfortable idle)
max_overflow=15  # burst capacity (total = 20, under the budget of 22)
```

### Environment Profiles

**Development (local):**

```python
create_async_engine(url, pool_size=2, max_overflow=3, pool_pre_ping=True)
```

**Low-traffic service (1-2 instances):**

```python
create_async_engine(
    url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
)
```

**Standard API (4-8 instances, DB max_connections=100):**

```python
# Per instance: 100 / 4 instances = 25 max, use 20 for safety
create_async_engine(
    url,
    pool_size=10,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_timeout=30,
)
```

**High-traffic API (10+ instances, DB max_connections=500):**

```python
# Per instance: 500 / 10 = 50, use 40 for safety
create_async_engine(
    url,
    pool_size=15,
    max_overflow=25,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_timeout=10,  # fail fast under extreme load
)
```

### Kubernetes Considerations

In Kubernetes, `max_overflow` matters more because the number of pods can vary:

```python
import os

POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))

engine = create_async_engine(
    DATABASE_URL,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_pre_ping=True,
)
```

Configure via environment variables so you can tune without code changes.

---

## 4. PgBouncer — External Connection Pooler

SQLAlchemy's pool manages connections **within a single process**. Under heavy load with many app instances, you can exhaust PostgreSQL's `max_connections` even with small per-instance pools.

**PgBouncer** sits between your app and PostgreSQL, multiplexing connections:

```
App Instance 1 (pool: 10 conns)  ─┐
App Instance 2 (pool: 10 conns)  ─┤→ PgBouncer (10 server conns) → PostgreSQL
App Instance 3 (pool: 10 conns)  ─┘
App Instance 4 (pool: 10 conns)

Without PgBouncer: 40 PostgreSQL connections
With PgBouncer:    10 PostgreSQL connections
```

### PgBouncer Modes

| Mode | How it works | Connection overhead | Use case |
|------|-------------|-------------------|----------|
| **Transaction** (recommended) | Server connection assigned per transaction | Lowest — reuses aggressively | Most web apps |
| **Session** | Server connection held for client's lifetime | Moderate | Apps using session-scoped features (advisory locks, SET LOCAL) |
| **Statement** | Server connection per statement | Lowest overhead | Read-only apps only — no multi-statement transactions |

**Transaction mode** is almost always the right choice. It means 100+ app connections can share 10-20 PostgreSQL connections, as long as they're not all in a transaction simultaneously.

### PgBouncer Configuration (pgbouncer.ini)

```ini
[databases]
mydb = host=postgres-server port=5432 dbname=mydb

[pgbouncer]
listen_port = 6432
listen_addr = 0.0.0.0
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt

pool_mode = transaction
default_pool_size = 20       # server connections per database/user pair
max_client_conn = 1000       # max simultaneous client connections
min_pool_size = 5            # minimum server connections to keep
reserve_pool_size = 5        # extra connections for bursts
reserve_pool_timeout = 5     # seconds before using reserve pool
server_idle_timeout = 600    # close idle server connections after 10 min
client_idle_timeout = 0      # never close idle client connections
```

### Connecting Through PgBouncer

```python
from sqlalchemy.pool import NullPool

# Point your connection string at PgBouncer instead of PostgreSQL directly
DATABASE_URL = "postgresql+asyncpg://user:pass@pgbouncer-host:6432/mydb"

# CRITICAL with PgBouncer transaction mode — two separate things to get right:
#   1. Don't double-pool: let PgBouncer be the pool (NullPool, or a tiny QueuePool).
#   2. Disable client-side prepared statements (see "PgBouncer Limitations" below) —
#      this is mandatory in transaction mode regardless of pool choice.
engine = create_async_engine(
    DATABASE_URL,
    poolclass=NullPool,                       # no internal pool — PgBouncer pools instead
    connect_args={"statement_cache_size": 0}, # MANDATORY in transaction mode
)

# Note: NullPool and pool_size/max_overflow are mutually exclusive — NullPool ignores
# size args. If you prefer a tiny pool instead of NullPool, drop poolclass and use:
#     pool_size=2, max_overflow=5, pool_pre_ping=True
# but keep connect_args={"statement_cache_size": 0} either way.
```

**Why use `NullPool` or a tiny pool with PgBouncer?**

With PgBouncer in transaction mode, SQLAlchemy's internal pool holds connections open even between transactions. PgBouncer can't reuse those held connections for other clients. To let PgBouncer do its job, either use `NullPool` (each request opens/closes a PgBouncer connection, and PgBouncer reuses server connections) or use a very small pool.

### PgBouncer Limitations

In transaction mode, these PostgreSQL features break because they depend on persistent server-side state:

| Feature | Problem |
|---------|---------|
| `SET LOCAL` / `SET SESSION` | Cleared between transactions |
| Advisory locks (`pg_advisory_lock`) | Tied to session, not transaction |
| `LISTEN` / `NOTIFY` | Session-scoped |
| Temporary tables (`CREATE TEMP TABLE`) | Session-scoped |
| Prepared statements (named) | Session-scoped — disable in driver |
| Cursors outside transactions | Session-scoped |

For prepared statements with asyncpg through PgBouncer:

```python
# asyncpg automatically prepares statements — disable this with PgBouncer.
# Set BOTH: the SQLAlchemy-level cache (prepared_statement_cache_size) and the
# asyncpg-native cache (statement_cache_size, passed via connect_args).
engine = create_async_engine(
    DATABASE_URL,
    prepared_statement_cache_size=0,              # SQLAlchemy-level
    connect_args={"statement_cache_size": 0},     # asyncpg-level
)
```

For psycopg3:

```python
engine = create_async_engine(
    DATABASE_URL,
    connect_args={"prepare_threshold": None},  # disable auto-prepare
)
```

---

## 5. asyncpg's Built-in Pool

asyncpg has its own pool, independent of SQLAlchemy. Use this when you're using asyncpg directly (without SQLAlchemy).

```python
import asyncpg

pool = await asyncpg.create_pool(
    dsn="postgresql://user:pass@localhost:5432/mydb",
    min_size=5,                          # always keep this many connections open
    max_size=20,                         # never more than this
    max_inactive_connection_lifetime=300, # close idle connections after 5 min
    command_timeout=30,                  # default timeout for all queries
    max_queries=50000,                   # recycle connection after N queries
    setup=setup_connection,              # called on each new connection
)
```

### Per-Connection Setup Hook

Use the `setup` parameter to configure each connection when it's created:

```python
async def setup_connection(conn):
    # Set default timezone
    await conn.execute("SET TIME ZONE 'UTC'")
    # Set statement timeout
    await conn.execute("SET statement_timeout = '30s'")
    # Register custom type codecs
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )

pool = await asyncpg.create_pool(dsn=DSN, setup=setup_connection)
```

---

## 6. Monitoring the Pool

### SQLAlchemy Pool Events

```python
from sqlalchemy import event
from sqlalchemy.pool import Pool
import logging

logger = logging.getLogger("sqlalchemy.pool")

@event.listens_for(Pool, "checkout")
def on_checkout(dbapi_conn, connection_record, connection_proxy):
    logger.debug("Connection checked out from pool")

@event.listens_for(Pool, "checkin")
def on_checkin(dbapi_conn, connection_record):
    logger.debug("Connection returned to pool")

@event.listens_for(Pool, "connect")
def on_connect(dbapi_conn, connection_record):
    logger.info("New database connection created")

@event.listens_for(Pool, "invalidate")
def on_invalidate(dbapi_conn, connection_record, exception):
    logger.warning("Database connection invalidated", exc_info=exception)
```

### Pool Status

```python
# Get pool statistics
pool = engine.pool
print(f"Pool size: {pool.size()}")
print(f"Checked out: {pool.checkedout()}")
print(f"Overflow: {pool.overflow()}")
print(f"Checked in: {pool.checkedin()}")
```

Expose these as metrics (Prometheus, Datadog):

```python
from prometheus_client import Gauge

db_pool_size = Gauge("db_pool_size", "Pool size")
db_pool_checked_out = Gauge("db_pool_checked_out", "Checked out connections")
db_pool_overflow = Gauge("db_pool_overflow", "Overflow connections")

async def update_pool_metrics():
    pool = engine.pool
    db_pool_size.set(pool.size())
    db_pool_checked_out.set(pool.checkedout())
    db_pool_overflow.set(pool.overflow())
```

### PostgreSQL-Side Monitoring

```sql
-- Active connections per application
SELECT application_name, state, count(*)
FROM pg_stat_activity
GROUP BY application_name, state
ORDER BY count DESC;

-- Long-running queries (potential connection hogs)
SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state
FROM pg_stat_activity
WHERE state != 'idle' AND query_start < now() - interval '30 seconds'
ORDER BY duration DESC;

-- Connections approaching max
SELECT count(*) AS active,
       max_conn,
       count(*) * 100 / max_conn AS pct_used
FROM pg_stat_activity,
     (SELECT setting::int AS max_conn FROM pg_settings WHERE name = 'max_connections') x
GROUP BY max_conn;

-- Kill a specific connection
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid = 12345;
```

---

## 7. Connection Pool Failure Modes

### Pool Exhaustion

**Symptom**: `TimeoutError: QueuePool limit of size N overflow N reached, connection timed out`

**Causes**:
- Long-running queries holding connections
- Missing `await session.close()` / not using context managers
- Sudden traffic spike
- Pool size too small for the workload

**Fixes**:
```python
# 1. Always use context managers — they guarantee return to pool
async with async_session() as session:
    ...  # session returned even on exception

# 2. Set statement timeout to prevent runaway queries
engine = create_async_engine(
    DATABASE_URL,
    connect_args={"options": "-c statement_timeout=30000"},  # 30s in ms
)

# 3. Increase pool size if the workload genuinely needs more connections
# 4. Set query timeouts to prevent connection hogging
```

### Stale Connections (without pre_ping)

**Symptom**: `OperationalError: SSL connection has been closed unexpectedly` or `server closed the connection unexpectedly`

**Cause**: Cloud databases (RDS, Cloud SQL) and proxies silently close idle connections after a timeout.

**Fix**: `pool_pre_ping=True` — always.

### Connection Leak

**Symptom**: Pool exhausts even with low traffic. Connection count slowly grows. Restart fixes it temporarily.

**Cause**: Code path that acquires a session but never releases it:

```python
# ❌ Connection leak
session = async_session()
result = await session.execute(select(User))
# exception here — session never closed!

# ✅ No leak — context manager guarantees cleanup
async with async_session() as session:
    result = await session.execute(select(User))
    # session closed on exit even if exception
```

**Detection**:

```sql
-- Find connections that have been idle in a transaction for > 5 minutes
SELECT pid, state, now() - xact_start AS tx_duration, query
FROM pg_stat_activity
WHERE state = 'idle in transaction'
AND xact_start < now() - interval '5 minutes';
```

### Thundering Herd on Startup

**Symptom**: App startup causes a spike of connection attempts that overwhelm the database.

**Fix**: SQLAlchemy creates connections lazily by default (first use, not startup). For asyncpg's own pool:

```python
pool = await asyncpg.create_pool(
    dsn=DSN,
    min_size=0,   # start with 0 connections, create on first request
    max_size=20,
)
```

---

## 8. Statement and Query Timeouts

Always set timeouts to prevent slow queries from holding connections and blocking the pool.

### PostgreSQL Statement Timeout

```python
# Set per-connection at creation time
engine = create_async_engine(
    DATABASE_URL,
    connect_args={"options": "-c statement_timeout=30000"},  # 30 seconds (ms)
)

# Set per-query via SQL
async with session.begin():
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    # expensive query here — will raise if it takes > 5s
```

### Lock Timeout

Prevent queries from waiting indefinitely for a lock:

```python
engine = create_async_engine(
    DATABASE_URL,
    connect_args={"options": "-c lock_timeout=5000"},  # 5 seconds
)
```

### Idle in Transaction Timeout

Prevent transactions from staying open indefinitely (a common connection leak source):

```python
engine = create_async_engine(
    DATABASE_URL,
    connect_args={"options": "-c idle_in_transaction_session_timeout=60000"},  # 60s
)
```

### asyncpg Command Timeout

```python
pool = await asyncpg.create_pool(
    dsn=DSN,
    command_timeout=30,  # default timeout for all queries (seconds)
)

# Per-query timeout
row = await pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id, timeout=5.0)
```

---

## 9. Production Checklist

```
Pool Configuration:
  ✅ pool_pre_ping=True — always, prevents stale connection errors
  ✅ pool_size + max_overflow < DB max_connections / num_instances
  ✅ pool_recycle set if using cloud DB, PgBouncer, or proxies
  ✅ pool_timeout set (prefer fast failure over long queues)
  ✅ statement_timeout set — prevents runaway queries

Code Patterns:
  ✅ Always use context managers for sessions (async with async_session() as s)
  ✅ One session per request, never shared between requests
  ✅ Engine created once at module level, never per-request
  ✅ engine.dispose() called on shutdown (lifespan context manager)

Monitoring:
  ✅ Pool metrics exposed (size, checked_out, overflow)
  ✅ Alerts on pool exhaustion (checked_out approaching max)
  ✅ Alerts on long-running queries (pg_stat_activity)
  ✅ Alerts on idle-in-transaction connections

PgBouncer (if used):
  ✅ Transaction mode (unless you use session-scoped features)
  ✅ Prepared statement cache disabled in driver
  ✅ SQLAlchemy pool minimized (NullPool or pool_size=1-2)
```
