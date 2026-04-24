# 08 — Database Testing

> **Purpose**: Run tests against a database fast and deterministically, without "works on CI, breaks in prod".

Mocking the database is usually wrong. Tests that pass against mocks but fail against real Postgres are the canonical source of broken migrations, silently-wrong queries, and constraint violations that only surface in production.

This guide covers three families of strategies, and when mocks are still the right call.

---

## Strategy Comparison

| Strategy | Speed | Isolation | Complexity | Best For |
|----------|-------|-----------|------------|----------|
| Drop/create per module | Slow | Full | Low | Small test suites |
| Transaction rollback | Fast | Full | Medium | Most projects |
| In-memory SQLite | Fastest | Full | Low | Unit-level DB tests |
| testcontainers (real Postgres) | Medium | Full | Medium | Production parity, CI |
| pytest-postgresql | Fast | Full | Low | Local dev with host Postgres |
| Shared test DB + cleanup | Medium | Fragile | Low | Legacy codebases only |

> **Warning**: In-memory SQLite behaves differently from PostgreSQL. Use it for logic tests, not for features that rely on Postgres-specific behavior (JSONB, arrays, `ON CONFLICT`, advisory locks, CTEs with `MATERIALIZED`).

---

## Strategy 1: Fresh Database Per Test Module

Simplest, slowest. Good for small suites or CI where clarity matters more than speed.

```python
# tests/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base
from app.main import app
from app.dependencies import get_db
from fastapi.testclient import TestClient

engine = create_engine("sqlite:///./test.db")
TestingSessionLocal = sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(setup_database):
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
```

---

## Strategy 2: Transaction Rollback (Fast, Isolated)

Instead of creating/dropping tables for every test, wrap each test in a transaction and roll it back:

```python
@pytest.fixture
def db_session(setup_database):
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()
```

Why this is better:

- Each test starts with a clean state.
- No `DELETE` queries needed.
- Much faster than `drop_all` / `create_all`.
- Tests are fully isolated, even across files.

### The `commit()` Problem

Transaction rollback gets tricky if application code calls `session.commit()` inside the test. The robust SQLAlchemy 2.x pattern is to bind the session to an already-open connection transaction and use **nested transactions with `SAVEPOINT`**, so user-level commits release savepoints while the outer transaction can still roll back.

```python
@pytest.fixture
def db_session(setup_database):
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()
```

`join_transaction_mode="create_savepoint"` was added in SQLAlchemy 2.0 and replaces the older `begin_nested` / event-listener pattern for joining a `Session` into an external transaction. If you are on SQLAlchemy < 2.0, upgrade — the ergonomics here alone justify it.

---

## Strategy 3: Async Database Testing (SQLAlchemy 2.0 + asyncpg)

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

ASYNC_TEST_DB_URL = "postgresql+asyncpg://user:pass@localhost/test_db"
async_engine = create_async_engine(ASYNC_TEST_DB_URL)
AsyncTestingSession = async_sessionmaker(async_engine, class_=AsyncSession)


@pytest.fixture
async def async_db_session():
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Open a connection-level transaction and let every session op nest inside
    # a SAVEPOINT, so a single rollback at the end always resets the state —
    # even if the test called commit().
    async with async_engine.connect() as conn:
        trans = await conn.begin()
        async with AsyncTestingSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
        ) as session:
            yield session
        await trans.rollback()

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
```

For a production-parity version (real Postgres in a container), see the `testcontainers` section below.

---

## `testcontainers-python` — Real Postgres, Ephemeral

Spins up a real Postgres container per test session, tears it down after. Docker required; works in local dev and most CI providers.

```python
# conftest.py
import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("postgresql://", "postgresql+asyncpg://")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine(postgres_url):
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine):
    """A session that rolls back after each test — isolation without teardown cost."""
    async with engine.connect() as conn:
        tx = await conn.begin()
        Session = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        session = Session()
        try:
            yield session
        finally:
            await session.close()
            await tx.rollback()
```

**Pros:** real database engine, real constraints, real SQL dialect — catches Postgres-only bugs (JSONB, arrays, `ON CONFLICT`, extensions).
**Cons:** Docker dependency (CI must support Docker-in-Docker or use services). Container startup adds ~5 s to the first test; `scope="session"` amortizes that.

> **Use `loop_scope="session"`** for session-scoped async fixtures like this. It keeps the engine on a session-lived event loop while ordinary per-test fixtures can stay function-scoped. See [06](06_async_testing.md).

---

## `pytest-postgresql` — No Docker, Host Postgres

Installs and manages a Postgres instance as a pytest plugin. No Docker — requires Postgres binaries on the host.

```python
# conftest.py
from pytest_postgresql import factories

postgresql_proc = factories.postgresql_proc(port=None)
postgresql = factories.postgresql("postgresql_proc")


def test_uses_postgres(postgresql):
    cur = postgresql.cursor()
    cur.execute("SELECT 1")
    assert cur.fetchone() == (1,)
```

Lighter weight than testcontainers but requires host Postgres. Fine when you control the CI image. Awkward on macOS unless everyone has `postgresql` installed via Homebrew.

---

## Seeding Test Data

For anything beyond trivial fixtures, prefer a **factory** pattern (see [07 — Fixtures](07_fixtures.md)) over hand-written SQL or ORM seeds:

```python
@pytest.fixture
def make_user(db_session):
    counter = {"n": 0}

    def _make(username=None, email=None, role="user"):
        counter["n"] += 1
        n = counter["n"]
        user = User(
            username=username or f"user_{n}",
            email=email or f"user_{n}@example.com",
            role=role,
        )
        db_session.add(user)
        db_session.flush()
        return user

    return _make
```

Unique defaults per call means you can create ten users in one test without email/username collisions.

---

## When Mocks Are Still Appropriate

- **Unit tests for pure logic** that takes a session argument but never runs real queries. Mock the session — see [03](03_unit_testing.md).
- **Validation tests** before a DB call — no point hitting the DB if the request is rejected at the schema layer.
- **Error-path tests** where simulating `IntegrityError` is easier than crafting a real one.

For anything that runs SQL you care about, prefer real Postgres. The infrastructure cost is low; the bugs caught are high-value.

---

## Migration Testing

A frequently-overlooked layer. Alembic migrations break silently — tests pass because they use `Base.metadata.create_all()`, and then the first production deploy hits a column mismatch.

The check that catches this:

```python
def test_migrations_match_models(engine):
    """Apply all migrations, then assert the schema matches SQLAlchemy models."""
    from alembic.config import Config
    from alembic import command

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(alembic_cfg, "head")

    # Compare live schema to models — fails if a model was added without a migration
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        mc = MigrationContext.configure(conn)
        diff = compare_metadata(mc, Base.metadata)
    assert diff == [], f"Schema drift detected: {diff}"
```

See [fundamentals/database/06_alembic.md](../../fundamentals/database/06_alembic.md) for the full migration strategy.

---

## Next

- [09 — Mocking External Services](09_mocking_external.md) — when the thing you're calling is not a database.
