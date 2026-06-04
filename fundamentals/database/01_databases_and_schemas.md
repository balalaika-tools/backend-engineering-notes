# Databases, Schemas, and SQL Foundations

> **Who this is for**: Engineers who know how to code but want a solid mental model of relational databases before touching Python drivers or ORMs.

---

## 1. What Is a Relational Database?

A relational database stores data in **tables** (relations). Each table has a fixed set of **columns** (the structure) and a variable number of **rows** (the data). Tables can reference each other through **foreign keys**, forming a web of related data.

```
users table                    posts table
─────────────────────          ──────────────────────────────────
id | email       | name        id | title    | author_id | published
───┼─────────────┼────         ───┼──────────┼───────────┼─────────
1  | a@b.com     | Alice       1  | Hello    | 1         | true
2  | x@y.com     | Bob         2  | World    | 1         | false
                               3  | Intro    | 2         | true
```

`posts.author_id` is a **foreign key** — it points to `users.id`. This is how you express "a post belongs to a user" without duplicating user data in every row.

### Why Relational?

- **Data integrity**: The database enforces rules (unique emails, non-null names, valid foreign keys).
- **No duplication**: Data lives in one place. Update a user's email once, everywhere sees the change.
- **Flexible queries**: SQL lets you slice and combine data any way you need.
- **ACID guarantees**: Transactions are reliable even if the server crashes mid-operation.

---

## 2. PostgreSQL vs SQLite — Choosing the Right Tool

### PostgreSQL

Full-featured, production-grade database. Use it for any application that will run in production.

| Feature | Detail |
|---------|--------|
| Concurrency | MVCC — many readers/writers simultaneously without blocking |
| Data types | Rich: `JSONB`, arrays, `UUID`, `INET`, `TSVECTOR`, ranges |
| Indexes | B-tree, Hash, GIN (for JSONB/arrays), GiST, BRIN |
| Full-text search | Built-in `tsvector` + `tsquery` |
| Replication | Streaming replication, logical replication (see §Replication and Read Replicas below) |
| Extensions | `pgvector` (embeddings), `PostGIS` (geospatial), `pg_trgm` (fuzzy search) |
| Max DB size | No practical limit (tested to petabytes) |

**When to use PostgreSQL**: Always. For web apps, APIs, microservices — PostgreSQL is the default right choice.

### SQLite

File-based, zero-configuration, serverless. The database is a single `.db` file.

```python
# Entire database is one file
"sqlite:///./local.db"
"sqlite+aiosqlite:///./local.db"  # async
":memory:"                         # in-memory, lost when process ends
```

| Feature | Detail |
|---------|--------|
| Setup | None — just a file |
| Concurrency | Only one writer at a time (WAL mode improves this) |
| Data types | Dynamic typing (not enforced) |
| Max size | ~281 TB theoretical, impractical above ~100 GB |
| Network | No — must be on same machine |

**When to use SQLite**:
- Local development (no Docker needed)
- Unit and integration tests (fast, clean state per test)
- CLI tools, scripts, small embedded apps
- Never in production for a web server with concurrent writes

### The SQLite-to-PostgreSQL Switch

During development you might use SQLite, then switch to PostgreSQL for production. The switch is mostly painless with SQLAlchemy, but watch out for:

| Difference | SQLite | PostgreSQL |
|-----------|--------|------------|
| Boolean | `0`/`1` integers | Native `BOOL` |
| Auto-increment | `INTEGER PRIMARY KEY` | `SERIAL` or `GENERATED ALWAYS AS IDENTITY` |
| `LIKE` operator | Case-insensitive for ASCII | Case-sensitive (use `ILIKE`) |
| `JSON` type | Stored as text | Native `JSONB` with indexing |

**Recommendation**: Use PostgreSQL everywhere, including local dev (via Docker). Eliminates the "works on my machine" class of bugs.

---

## 3. Core Concepts: Tables, Columns, and Data Types

### Tables and Columns

A **table** maps to a real-world entity: users, orders, products.
A **column** is an attribute of that entity with a specific type.
A **row** is a single instance.

```sql
CREATE TABLE users (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(100) NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### PostgreSQL Data Types You Will Actually Use

| Type | Use case | Example |
|------|----------|---------|
| `INTEGER` / `BIGINT` | Whole numbers, IDs | `42`, `-7` |
| `SERIAL` / `BIGSERIAL` | Auto-increment integers | Primary keys |
| `UUID` | Universally unique IDs | `gen_random_uuid()` |
| `VARCHAR(n)` | Short strings with max length | Emails, names |
| `TEXT` | Unlimited text | Content, descriptions |
| `BOOLEAN` | True/false | `TRUE`, `FALSE` |
| `NUMERIC(p,s)` | Exact decimals | Money: `NUMERIC(10,2)` |
| `FLOAT` / `DOUBLE PRECISION` | Approximate decimals | Coordinates, percentages |
| `TIMESTAMP` | Date + time, no timezone | Internal timestamps |
| `TIMESTAMPTZ` | Date + time with timezone | **Prefer this for user-facing times** |
| `DATE` | Date only | Birthdays, due dates |
| `JSONB` | Binary JSON, indexable | Flexible data, config, events |
| `ARRAY` | Array of a type | `INTEGER[]`, `TEXT[]` |
| `INET` | IP address | Client IPs |
| `TSVECTOR` | Full-text search | Search indexes |

### JSONB — PostgreSQL's Superpower

`JSONB` stores JSON as binary, not text. It's compressed, indexed, and queryable:

```sql
-- Querying inside JSONB
SELECT * FROM events WHERE payload->>'event_type' = 'login';

-- Index a specific key
CREATE INDEX ON events ((payload->>'user_id'));

-- GIN index for arbitrary key queries
CREATE INDEX ON events USING GIN (payload);
```

Use `JSONB` when:
- Data is schemaless or highly variable (audit logs, config, metadata)
- You need to store arbitrary key-value pairs
- You want to avoid a schema migration for every new field

Avoid `JSONB` when the data is structured and you need to filter/join on its contents frequently — a proper column with an index will always be faster.

---

## 4. Constraints — Enforcing Data Integrity at the Database Level

Constraints are rules the database enforces on every write. They are your last line of defense.

### PRIMARY KEY

Uniquely identifies each row. Cannot be NULL. Usually an auto-increment integer or UUID.

```sql
id SERIAL PRIMARY KEY
id UUID PRIMARY KEY DEFAULT gen_random_uuid()
```

### UNIQUE

No two rows can have the same value(s). Can span multiple columns.

```sql
email VARCHAR(255) UNIQUE                          -- single column
UNIQUE (user_id, team_id)                          -- composite unique (no duplicate memberships)
```

### NOT NULL

Column must always have a value. Without this, `NULL` is allowed (and `NULL != NULL` in SQL, which causes bugs).

```sql
name VARCHAR(100) NOT NULL
```

### FOREIGN KEY

Links rows between tables. Prevents orphaned records.

```sql
author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
```

`ON DELETE` behaviors:

| Behavior | What happens when parent row is deleted |
|----------|----------------------------------------|
| `RESTRICT` | Block the delete (error) — default |
| `CASCADE` | Delete child rows automatically |
| `SET NULL` | Set the FK column to NULL |
| `SET DEFAULT` | Set the FK column to its default value |

### CHECK

Validates a condition on the row:

```sql
age INTEGER CHECK (age >= 0 AND age <= 150)
status VARCHAR(20) CHECK (status IN ('pending', 'active', 'cancelled'))
```

### DEFAULT

Sets the value when none is provided:

```sql
is_active   BOOLEAN NOT NULL DEFAULT TRUE
created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

---

## 5. Indexes — Making Queries Fast

An index is a separate data structure (usually a B-tree) that PostgreSQL maintains to allow fast lookups.

### Without an Index

```sql
SELECT * FROM users WHERE email = 'alice@example.com';
-- PostgreSQL scans every row (sequential scan) — O(n)
```

### With an Index

```sql
CREATE INDEX ON users (email);
SELECT * FROM users WHERE email = 'alice@example.com';
-- PostgreSQL jumps directly to the row — O(log n)
```

### Index Types

| Type | Use case | Example |
|------|----------|---------|
| B-tree (default) | Equality, range queries (`=`, `<`, `>`, `BETWEEN`) | Most columns |
| Hash | Equality only (`=`) — smaller, faster for equality | `id = $1` |
| GIN | JSONB, arrays, full-text search | `payload @> '{"type":"login"}'` |
| GiST | Geometric data, full-text | PostGIS, `tsvector` |
| BRIN | Very large tables with naturally ordered data | Append-only logs by timestamp |

### When to Add an Index

Add indexes on columns you:
- Filter on (`WHERE email = $1`)
- Sort on frequently (`ORDER BY created_at DESC`)
- Join on (`JOIN posts ON posts.author_id = users.id`)
- Use as a foreign key

**Unique constraint = unique index.** When you declare `UNIQUE`, PostgreSQL creates the index automatically.

### Index Cost

Indexes are not free:
- Every `INSERT`, `UPDATE`, `DELETE` must update all indexes on that table
- Indexes use disk space
- Too many indexes on a write-heavy table can slow it down

Rule of thumb: add indexes for your known query patterns. Don't index everything preemptively. Use `EXPLAIN ANALYZE` to see if a query is using indexes.

```sql
EXPLAIN ANALYZE SELECT * FROM users WHERE email = 'alice@example.com';
```

### Reading `EXPLAIN ANALYZE` Output

`EXPLAIN` shows the plan the optimizer will use. `EXPLAIN ANALYZE` actually runs the query and reports real numbers. The output is a tree of nodes; read bottom-up (leaves run first).

```
Index Scan using users_email_idx on users  (cost=0.42..8.44 rows=1 width=64) (actual time=0.041..0.043 rows=1 loops=1)
  Index Cond: (email = 'alice@example.com'::text)
Planning Time: 0.108 ms
Execution Time: 0.071 ms
```

What each piece means:

| Piece | Meaning |
|-------|---------|
| `Index Scan using users_email_idx` | Used an index. Seeking to rows directly. |
| `Seq Scan on users` | Reading the whole table. Fine for small tables; a red flag on large ones with a `WHERE` that should be indexable. |
| `Bitmap Index Scan` / `Bitmap Heap Scan` | Mid-selectivity query: index gives row locations, heap is read in sorted order. |
| `cost=0.42..8.44` | Planner's estimated cost units (startup..total). Arbitrary, but comparable across plans. |
| `rows=1` | Planner's row estimate. Compare against actual — mismatch by 10×+ means stale statistics (run `ANALYZE tablename`). |
| `actual time=0.041..0.043` | Wall time, ms. First number is time to first row, second is time to last row. |
| `loops=N` | Node executed N times (once per outer row in a nested loop join). Total time = per-loop × loops. |
| `Planning Time` / `Execution Time` | Planning cost (usually sub-ms) and real execution. If planning is a large fraction of total, the query is fast enough to not care. |

**What to look for when tuning:**

- `Seq Scan` on a large table with a selective `WHERE` → add the right index.
- Large gap between estimated `rows=` and actual rows in the output → `ANALYZE` the table (autovacuum usually handles this, but bulk loads may outrun it).
- `Rows Removed by Filter: N` after an index scan → index matched too broadly; consider a composite or partial index.
- Nested Loop with high `loops=` → outer side is big; maybe a Hash Join would be better. PostgreSQL usually picks right, but very skewed data or wrong stats can mislead it.

### Partial Indexes

A **partial index** indexes only the rows matching a `WHERE` clause. Smaller on disk, faster to maintain, and often faster to query — because the index is exactly the subset you filter on.

```sql
-- Most orders are completed; only a small fraction are 'active'.
-- A full index on status is huge and mostly useless for the "active" query.
CREATE INDEX idx_orders_active ON orders (user_id, created_at)
    WHERE status = 'active';
```

Now:

```sql
SELECT * FROM orders
 WHERE user_id = 42
   AND status = 'active'
 ORDER BY created_at DESC;
```

…uses this partial index, which contains only active rows. The index is a fraction of the size of a full `(user_id, created_at)` index and reads are proportionally faster.

Other good partial-index targets:

- **Soft-delete columns**: `WHERE deleted_at IS NULL` — exclude the dead rows from the index.
- **Flag columns with lopsided distribution**: `WHERE flagged = true` — the 0.1% of rows flagged for review.
- **Recent activity**: `WHERE created_at > now() - interval '30 days'` — but this becomes stale; pair with a job that rebuilds the index (or prefer time-based partitioning).

Cost to remember: PostgreSQL can only use a partial index when the query's `WHERE` clause is a superset of the index predicate. A query without `status = 'active'` ignores this index entirely.

---

## 6. Schemas — Namespacing Tables

In PostgreSQL, a **schema** is a namespace inside a database. Think of it like a folder.

```
database: myapp
├── schema: public (default)
│   ├── users
│   ├── posts
│   └── comments
├── schema: analytics
│   ├── events
│   └── sessions
└── schema: auth
    ├── tokens
    └── sessions
```

```sql
-- Access a table in a specific schema
SELECT * FROM analytics.events;

-- Create a schema
CREATE SCHEMA analytics;

-- Set the search path (which schemas to check by default)
SET search_path = public, analytics;
```

In most Python apps with a single database, you only use `public`. Schemas become useful for:
- Multi-tenant apps (one schema per tenant)
- Logical separation of concerns (auth, core, reporting)
- Preventing name collisions when using multiple modules

---

## 7. Transactions and ACID

A **transaction** groups multiple SQL statements into a single atomic unit. Either all statements succeed, or none of them do.

```sql
BEGIN;
    UPDATE accounts SET balance = balance - 100 WHERE id = 1;
    UPDATE accounts SET balance = balance + 100 WHERE id = 2;
COMMIT;
-- Both updates committed, or neither is
```

If anything goes wrong between `BEGIN` and `COMMIT`:

```sql
BEGIN;
    UPDATE accounts SET balance = balance - 100 WHERE id = 1;
    -- server crashes here
-- PostgreSQL automatically rolls back — balance unchanged
```

### ACID Properties

| Property | Meaning | Example |
|----------|---------|---------|
| **Atomicity** | All or nothing — partial transactions don't exist | Money transfer: both debits and credits happen, or neither does |
| **Consistency** | Transaction leaves the database in a valid state | Constraints are checked before commit |
| **Isolation** | Concurrent transactions don't interfere with each other | Two users booking the last seat don't both succeed |
| **Durability** | Committed data survives crashes | Written to disk (WAL) before `COMMIT` returns |

### Isolation Levels

PostgreSQL supports four isolation levels. The default is `READ COMMITTED`.

| Level | Prevents | Use case |
|-------|----------|----------|
| `READ UNCOMMITTED` | Nothing (same as READ COMMITTED in PG) | N/A in PostgreSQL |
| `READ COMMITTED` | Dirty reads | Default — good for most apps |
| `REPEATABLE READ` | Dirty reads, non-repeatable reads | Reporting queries that must see consistent snapshot |
| `SERIALIZABLE` | All anomalies | Financial operations, strict correctness |

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ;
-- all reads see the same snapshot for the duration
COMMIT;
```

In Python, with SQLAlchemy, set the isolation level via `execution_options`
**before** the transaction starts — issuing a raw `SET TRANSACTION` after
`BEGIN` is too late and the driver may ignore or reject it:

```python
# Per-transaction: bind the level to the connection before opening the txn
conn = await session.connection(
    execution_options={"isolation_level": "REPEATABLE READ"}
)
async with session.begin():
    # ... queries run under REPEATABLE READ
    ...

# Or set it once on a dedicated engine (cleanest when a whole workload
# needs the same level):
engine = create_async_engine(DATABASE_URL, isolation_level="SERIALIZABLE")
```

### Savepoints — Nested Rollbacks

```sql
BEGIN;
    INSERT INTO users (email) VALUES ('a@b.com');
    SAVEPOINT sp1;
    INSERT INTO users (email) VALUES ('a@b.com');  -- duplicate!
    ROLLBACK TO SAVEPOINT sp1;                      -- undo only the second insert
    -- first insert is still in transaction
COMMIT;
```

Savepoints let you roll back part of a transaction without aborting the whole thing. SQLAlchemy exposes this via `session.begin_nested()`.

---

## 8. SQL Fundamentals

### SELECT — Reading Data

```sql
-- Basic
SELECT id, email, name FROM users;

-- All columns (avoid in production code — fragile against schema changes)
SELECT * FROM users;

-- Filter
SELECT * FROM users WHERE is_active = TRUE AND email LIKE '%@company.com';

-- Sort
SELECT * FROM users ORDER BY created_at DESC;

-- Paginate
SELECT * FROM users ORDER BY id LIMIT 20 OFFSET 40;

-- Count
SELECT COUNT(*) FROM users WHERE is_active = TRUE;
```

### JOINs — Combining Tables

```sql
-- INNER JOIN: only rows with a match in both tables
SELECT u.name, p.title
FROM posts p
INNER JOIN users u ON p.author_id = u.id;

-- LEFT JOIN: all rows from left table, matching rows from right (NULL if no match)
SELECT u.name, COUNT(p.id) AS post_count
FROM users u
LEFT JOIN posts p ON p.author_id = u.id
GROUP BY u.id, u.name;

-- EXISTS: check if related rows exist
SELECT * FROM users u
WHERE EXISTS (
    SELECT 1 FROM posts p WHERE p.author_id = u.id AND p.published = TRUE
);
```

### INSERT

```sql
-- Single row
INSERT INTO users (email, name) VALUES ('a@b.com', 'Alice');

-- Return generated values
INSERT INTO users (email, name) VALUES ('a@b.com', 'Alice') RETURNING id, created_at;

-- Multiple rows
INSERT INTO users (email, name) VALUES
    ('a@b.com', 'Alice'),
    ('x@y.com', 'Bob');

-- Upsert (insert or update on conflict)
INSERT INTO users (email, name) VALUES ('a@b.com', 'Alice')
ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name;

-- Ignore on conflict
INSERT INTO users (email, name) VALUES ('a@b.com', 'Alice')
ON CONFLICT (email) DO NOTHING;
```

### UPDATE

```sql
-- Basic update
UPDATE users SET name = 'Alice Smith' WHERE id = 1;

-- Update multiple columns
UPDATE users SET name = 'Alice Smith', is_active = FALSE WHERE id = 1;

-- Update with a subquery
UPDATE posts SET published = TRUE
WHERE author_id IN (SELECT id FROM users WHERE is_active = TRUE);

-- Return updated rows
UPDATE users SET is_active = FALSE WHERE id = 1 RETURNING id, email;
```

### DELETE

```sql
-- Delete specific rows
DELETE FROM users WHERE id = 1;

-- Delete with condition
DELETE FROM users WHERE created_at < NOW() - INTERVAL '1 year' AND is_active = FALSE;

-- Return deleted rows
DELETE FROM users WHERE id = 1 RETURNING email;

-- Truncate (fast delete all rows, non-transactional — careful!)
TRUNCATE TABLE users;
TRUNCATE TABLE users RESTART IDENTITY CASCADE;  -- also resets sequences and cascades
```

---

## 9. Advanced PostgreSQL Features

### Common Table Expressions (CTEs)

CTEs (`WITH` clauses) let you name and reuse subqueries. They make complex queries readable.

```sql
-- Readable multi-step query
WITH active_users AS (
    SELECT id, name FROM users WHERE is_active = TRUE
),
user_post_counts AS (
    SELECT author_id, COUNT(*) AS post_count FROM posts GROUP BY author_id
)
SELECT u.name, COALESCE(c.post_count, 0) AS post_count
FROM active_users u
LEFT JOIN user_post_counts c ON c.author_id = u.id
ORDER BY post_count DESC;
```

### Window Functions

Window functions compute a value for each row based on a "window" of related rows, without collapsing them like `GROUP BY` does.

```sql
-- Rank users by post count within each department
SELECT
    name,
    department,
    post_count,
    RANK() OVER (PARTITION BY department ORDER BY post_count DESC) AS rank_in_dept
FROM user_stats;

-- Running total
SELECT
    created_at,
    amount,
    SUM(amount) OVER (ORDER BY created_at) AS running_total
FROM transactions;

-- Previous row's value
SELECT
    created_at,
    temperature,
    LAG(temperature) OVER (ORDER BY created_at) AS prev_temp
FROM sensor_readings;
```

### JSONB Operators

```sql
-- Field access (returns text)
SELECT payload->>'user_id' FROM events;

-- Nested field access
SELECT payload->'metadata'->>'source' FROM events;

-- Contains (subset check)
SELECT * FROM events WHERE payload @> '{"event_type": "login"}';

-- Has key
SELECT * FROM events WHERE payload ? 'error_code';

-- Path exists
SELECT * FROM events WHERE payload #>> '{metadata,source}' = 'web';
```

### Full-Text Search

```sql
-- Create a tsvector column
ALTER TABLE posts ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || content)) STORED;

CREATE INDEX ON posts USING GIN (search_vector);

-- Search
SELECT title FROM posts
WHERE search_vector @@ to_tsquery('english', 'python & database');

-- Ranked search
SELECT title, ts_rank(search_vector, query) AS rank
FROM posts, to_tsquery('english', 'python & database') query
WHERE search_vector @@ query
ORDER BY rank DESC;
```

---

## 10. Database Design: Normalization

**Normalization** is the process of organizing tables to reduce redundancy and prevent data anomalies.

### First Normal Form (1NF)

Each column contains atomic (indivisible) values. No repeating groups.

```
Bad:  users.tags = "python,backend,api"    ← CSV in a column
Good: tags table with user_id FK           ← separate rows
```

### Second Normal Form (2NF)

Every non-key column depends on the **whole** primary key (relevant for composite keys).

### Third Normal Form (3NF)

No transitive dependencies: non-key columns shouldn't depend on other non-key columns.

```
Bad:  orders table has both customer_id AND customer_email
      (customer_email depends on customer_id, not on order_id)
Good: customer_email lives in the customers table
```

### When to Denormalize

Normalization reduces redundancy, but sometimes you need to **denormalize** for performance:

- **Precomputed counts**: `user.post_count` column instead of counting every time
- **Denormalized snapshots**: `orders.customer_name` captures the name at order time (immutable history)
- **Read-heavy analytics**: flattened tables avoid expensive JOINs

> Rule: normalize first, denormalize only when you have a measured performance problem.

---

## Quick Reference

### Useful psql Commands

```bash
\l                    # list databases
\c mydb               # connect to database
\dt                   # list tables
\dt+                  # list tables with sizes
\d users              # describe table
\di                   # list indexes
\df                   # list functions
\timing               # toggle query timing
\x                    # toggle expanded output
EXPLAIN ANALYZE ...;  # query plan + actual execution stats
```

### PostgreSQL System Queries

```sql
-- Active connections
SELECT * FROM pg_stat_activity;

-- Table sizes
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;

-- Unused indexes
SELECT * FROM pg_stat_user_indexes WHERE idx_scan = 0;

-- Slow queries (requires pg_stat_statements extension)
SELECT query, calls, mean_exec_time FROM pg_stat_statements
ORDER BY mean_exec_time DESC LIMIT 20;
```

---

## Replication and Read Replicas

Postgres replicates via the **Write-Ahead Log (WAL)**. Every change the primary makes is first written to the WAL; replicas stream that log and apply it. Understanding this mechanism is worth the five minutes:

### Streaming replication (the default for RDS / Aurora / self-hosted HA)

The primary ships WAL records to the replica continuously. The replica applies them, usually within a few milliseconds. **It's asynchronous** — the primary acks the client as soon as it writes locally; replicating to the replica is a separate step. Synchronous replication is a configuration option (`synchronous_commit = on`) but adds latency to every write, so most deployments stay async.

**Replication lag** is the time between a write committing on the primary and being visible on a replica. On a healthy system it's sub-second; under heavy write load it can grow to seconds or minutes. Monitoring lag is non-negotiable:

```sql
-- On the primary — shows lag per replica
SELECT client_addr, state, write_lag, flush_lag, replay_lag
  FROM pg_stat_replication;

-- On the replica — current lag against the primary
SELECT now() - pg_last_xact_replay_timestamp() AS lag;
```

Alert when lag exceeds your tolerance. Lag > 30s usually means the replica is underprovisioned (CPU, IO) or the primary is generating WAL faster than it can be streamed.

### Reading from replicas

The business reason to have replicas: **offload read traffic**. Reporting queries, dashboard aggregations, non-critical reads — send them to a replica so the primary can focus on writes.

```python
# Separate engines, separate pools
write_engine = create_async_engine(PRIMARY_URL, pool_size=20)
read_engine = create_async_engine(REPLICA_URL, pool_size=30)

# Dependencies pick based on intent
async def get_write_session():
    async with async_sessionmaker(write_engine)() as s:
        yield s

async def get_read_session():
    async with async_sessionmaker(read_engine)() as s:
        yield s
```

**Rule of thumb for safety:** do not read from a replica immediately after writing to the primary if the user expects to see their write. The 200ms of replication lag is plenty to produce "I just saved this and it's gone" bug reports. Options:
- Read the user's own writes from the primary (tag "my own data" queries as writable).
- Use a read-your-writes cache (write goes to primary + cache; reads check cache first).
- Accept the UX and clearly indicate "this may take a moment to appear."

### Logical replication

Logical replication replays changes at the **row level**, not WAL block level. This lets you replicate subsets of tables, replicate across Postgres versions, or feed the changes to a non-Postgres consumer (Kafka, Elasticsearch) via Debezium. Slower and more complex than streaming replication; use when you need the flexibility, not when you just want a hot standby.

---
