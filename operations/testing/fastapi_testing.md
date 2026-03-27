# Testing FastAPI Applications

A production-focused guide to testing FastAPI — from mental model to real-world patterns.

---

## 1. The Testing Mental Model

### Why Test?

Tests are not about proving code works. Tests are about **catching regressions** and **enabling fearless refactoring**.

Without tests:

- Every deploy is a gamble
- Refactoring is terrifying
- Bugs are discovered by users

With tests:

- You change code and know within seconds if something broke
- New team members can't silently break existing behavior
- Your CI pipeline becomes a safety net

### What to Test

Not everything deserves a test. Focus on **value per test**.

| Priority | What to Test | Why |
|----------|-------------|-----|
| 1 | Request/response contracts | Catches API-breaking changes |
| 2 | Business logic (services) | Core value of your app |
| 3 | Auth and permissions | Security regressions are expensive |
| 4 | Error handling paths | Users hit these more than you think |
| 5 | Edge cases (empty lists, nulls) | Where most production bugs hide |
| 6 | Integration with DB | Schema changes break things silently |

What NOT to test:

- FastAPI/Pydantic internals (they're already tested)
- Trivial pass-through endpoints with no logic
- Third-party library behavior

### The Test Pyramid

```
         /  E2E  \          Few — slow, brittle, expensive
        /----------\
       / Integration \      Some — test component boundaries
      /----------------\
     /    Unit Tests     \  Many — fast, isolated, cheap
    /____________________\
```

For FastAPI backends:

- **Unit tests** — pure functions, validators, business logic
- **Integration tests** — endpoints with test DB, dependency overrides
- **E2E tests** — full stack with external services (usually in CI only)

> **Rule of thumb**: If a test needs network access or takes > 1 second, it's integration, not unit.

---

## 2. Setup

### Install Dependencies

```bash
pip install pytest pytest-asyncio httpx respx
```

### Project Structure

```
project/
├── app/
│   ├── main.py
│   ├── models.py
│   ├── schemas.py
│   ├── services.py
│   ├── dependencies.py
│   └── routers/
│       └── users.py
├── tests/
│   ├── conftest.py          # shared fixtures
│   ├── test_users.py
│   ├── test_auth.py
│   └── test_services.py
├── pyproject.toml
```

### `pyproject.toml` Configuration

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"        # no need for @pytest.mark.anyio on every test
testpaths = ["tests"]
```

> Setting `asyncio_mode = "auto"` means all `async def test_*` functions are treated as async tests automatically. Without it, you must decorate every async test.

---

## 3. Testing FastAPI Endpoints

### The Two Approaches

| Feature | `TestClient` (sync) | `httpx.AsyncClient` (async) |
|---------|---------------------|----------------------------|
| Import | `from starlette.testclient import TestClient` | `from httpx import AsyncClient, ASGITransport` |
| Test style | `def test_x():` | `async def test_x():` |
| Event loop | Creates its own | Uses pytest-asyncio's loop |
| Lifespan events | Supported via `with` | Supported via `async with` |
| Async dependencies | Works (runs in thread) | Works natively |
| When to use | Simple sync endpoints | Async endpoints, async fixtures, async DB |

### Sync Testing with TestClient

```python
from starlette.testclient import TestClient
from app.main import app

def test_read_root():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "hello"}
```

`TestClient` wraps your ASGI app and runs it in a thread. Good for simple cases.

### Async Testing with httpx.AsyncClient

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.anyio
async def test_read_root():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "hello"}
```

### Why ASGITransport?

`ASGITransport` connects httpx directly to your ASGI app **in-process** — no real HTTP server, no real network. The `base_url` is required by httpx but never actually contacted.

```python
# This does NOT make a real HTTP request
# It calls your app's ASGI interface directly
transport = ASGITransport(app=app)
```

### Common Mistake: Forgetting ASGITransport

```python
# ❌ Wrong — this makes a REAL HTTP request to http://test
async with AsyncClient(base_url="http://test") as client:
    response = await client.get("/")  # ConnectionError

# ✅ Correct — routes through your ASGI app in-process
transport = ASGITransport(app=app)
async with AsyncClient(transport=transport, base_url="http://test") as client:
    response = await client.get("/")  # hits your app
```

---

## 4. Dependency Overrides

### The Core Mechanism

FastAPI's `app.dependency_overrides` is a dictionary that maps original dependencies to replacement callables.

```python
app.dependency_overrides[original_dependency] = fake_dependency
```

When FastAPI resolves `Depends(original_dependency)`, it calls `fake_dependency` instead.

### Overriding a Database Session

Production code:

```python
# app/dependencies.py
from sqlalchemy.orm import Session

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# app/routers/users.py
@router.get("/users/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404)
    return user
```

Test code:

```python
# tests/conftest.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.dependencies import get_db
from app.models import Base

SQLALCHEMY_TEST_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_TEST_URL)
TestingSessionLocal = sessionmaker(bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
```

### Overriding Authentication

Production code:

```python
# app/dependencies.py
async def get_current_user(token: str = Depends(oauth2_scheme)):
    user = await verify_token(token)
    if not user:
        raise HTTPException(status_code=401)
    return user
```

Test code:

```python
# tests/conftest.py
from app.dependencies import get_current_user

def override_get_current_user():
    return {"id": 1, "username": "testuser", "role": "admin"}

app.dependency_overrides[get_current_user] = override_get_current_user
```

Now all endpoints that depend on `get_current_user` receive the fake user **without needing a real token**.

### Critical Mistake: Not Cleaning Up Overrides

```python
# ❌ Wrong — override leaks into other tests
def test_something():
    app.dependency_overrides[get_db] = fake_db
    client = TestClient(app)
    response = client.get("/users/1")
    assert response.status_code == 200
    # override is still active for the next test!

# ✅ Correct — clean up after yourself
def test_something():
    app.dependency_overrides[get_db] = fake_db
    try:
        client = TestClient(app)
        response = client.get("/users/1")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()

# ✅ Better — use a fixture
@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()
```

---

## 5. Testing Async Code

### Configuring pytest-asyncio

With `asyncio_mode = "auto"` in `pyproject.toml`:

```python
# No decorator needed — pytest-asyncio detects async automatically
async def test_async_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/async-endpoint")
        assert response.status_code == 200
```

Without `asyncio_mode = "auto"`:

```python
# Must decorate every async test
@pytest.mark.anyio
async def test_async_endpoint():
    ...
```

### Async Fixtures

```python
@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

async def test_get_users(async_client):
    response = await async_client.get("/users")
    assert response.status_code == 200
```

### Common Mistake: Mixing Sync and Async

```python
# ❌ Wrong — sync test calling async fixture
def test_something(async_client):  # async_client is a coroutine, not a client
    response = async_client.get("/users")  # TypeError

# ✅ Correct — async test with async fixture
async def test_something(async_client):
    response = await async_client.get("/users")
```

---

## 6. Database Testing

### Strategy 1: Fresh Database Per Test Module

```python
# tests/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base
from app.main import app
from app.dependencies import get_db

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

### Strategy 2: Transaction Rollback (Fast, Isolated)

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

- Each test starts with a clean state
- No `DELETE` queries needed
- Much faster than `drop_all` / `create_all`
- Tests are fully isolated

### Strategy 3: Async Database Testing (SQLAlchemy 2.0 + asyncpg)

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

ASYNC_TEST_DB_URL = "postgresql+asyncpg://user:pass@localhost/test_db"
async_engine = create_async_engine(ASYNC_TEST_DB_URL)
AsyncTestingSession = async_sessionmaker(async_engine, class_=AsyncSession)

@pytest.fixture
async def async_db_session():
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncTestingSession() as session:
        async with session.begin():
            yield session
            await session.rollback()

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
```

### Database Testing Comparison

| Strategy | Speed | Isolation | Complexity | Best For |
|----------|-------|-----------|------------|----------|
| Drop/create per module | Slow | Full | Low | Small test suites |
| Transaction rollback | Fast | Full | Medium | Most projects |
| In-memory SQLite | Fastest | Full | Low | Unit-level DB tests |
| Shared test DB + cleanup | Medium | Fragile | Low | Legacy codebases |

> **Warning**: In-memory SQLite behaves differently from PostgreSQL. Use it for logic tests, not for testing queries that rely on Postgres-specific features (JSONB, arrays, `ON CONFLICT`).

---

## 7. Mocking External APIs

### The Problem

Your endpoint calls an external API. You don't want tests to:

- Hit real APIs (slow, flaky, costs money)
- Depend on network availability
- Mutate real data

### Option 1: `respx` (Best for httpx-based code)

`respx` intercepts httpx requests at the transport level. No monkey-patching.

```python
import respx
from httpx import Response

# app/services.py
async def fetch_weather(city: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.weather.com/{city}")
        response.raise_for_status()
        return response.json()

# tests/test_weather.py
@respx.mock
async def test_fetch_weather():
    respx.get("https://api.weather.com/london").mock(
        return_value=Response(200, json={"temp": 15, "city": "london"})
    )

    result = await fetch_weather("london")
    assert result["temp"] == 15
    assert result["city"] == "london"
```

### Testing Error Scenarios with respx

```python
@respx.mock
async def test_weather_api_timeout():
    respx.get("https://api.weather.com/london").mock(
        side_effect=httpx.ConnectTimeout("Connection timed out")
    )

    with pytest.raises(httpx.ConnectTimeout):
        await fetch_weather("london")

@respx.mock
async def test_weather_api_500():
    respx.get("https://api.weather.com/london").mock(
        return_value=Response(500, json={"error": "internal"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_weather("london")
```

### Option 2: `unittest.mock.AsyncMock` (For any async callable)

```python
from unittest.mock import AsyncMock, patch

# app/services.py
class PaymentService:
    async def charge(self, user_id: int, amount: float) -> dict:
        # calls Stripe API
        ...

# tests/test_payments.py
async def test_charge_user():
    mock_service = AsyncMock()
    mock_service.charge.return_value = {"status": "success", "id": "ch_123"}

    result = await mock_service.charge(user_id=1, amount=99.99)

    assert result["status"] == "success"
    mock_service.charge.assert_called_once_with(user_id=1, amount=99.99)
```

### Option 3: Patching with `patch`

```python
from unittest.mock import patch, AsyncMock

async def test_endpoint_with_mocked_service():
    with patch("app.routers.users.payment_service.charge", new_callable=AsyncMock) as mock_charge:
        mock_charge.return_value = {"status": "success"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/pay", json={"amount": 50})

        assert response.status_code == 200
        mock_charge.assert_called_once()
```

### When to Use What

| Tool | Best For | Intercepts At |
|------|----------|---------------|
| `respx` | httpx HTTP calls | Transport layer |
| `AsyncMock` | Any async function/method | Function call |
| `patch` | Replacing module-level objects | Import path |
| Dependency override | FastAPI dependencies | DI container |

> **Prefer dependency overrides** over `patch` when testing FastAPI endpoints. They're explicit, don't rely on import paths, and match how FastAPI actually resolves dependencies.

---

## 8. Fixture Patterns

### conftest.py Hierarchy

pytest discovers `conftest.py` files at every directory level:

```
tests/
├── conftest.py              # shared across ALL tests
├── api/
│   ├── conftest.py          # shared across api tests only
│   ├── test_users.py
│   └── test_orders.py
└── services/
    ├── conftest.py          # shared across service tests only
    └── test_payment.py
```

### Essential Fixtures

```python
# tests/conftest.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.fixture
async def client():
    """Async test client — use for all endpoint tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

@pytest.fixture(autouse=True)
def reset_dependency_overrides():
    """Ensure no override leaks between tests."""
    yield
    app.dependency_overrides.clear()
```

### Scoped Fixtures

| Scope | Lifecycle | Use Case |
|-------|-----------|----------|
| `function` (default) | Per test | DB sessions, clients |
| `class` | Per test class | Grouped related tests |
| `module` | Per file | Schema creation/teardown |
| `session` | Entire test run | Engine creation, app startup |

```python
@pytest.fixture(scope="session")
def engine():
    """One engine for the entire test run."""
    eng = create_engine(TEST_DB_URL)
    yield eng
    eng.dispose()

@pytest.fixture(scope="module")
def tables(engine):
    """Create tables once per test module."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def db_session(engine, tables):
    """Fresh session per test, rolled back after."""
    conn = engine.connect()
    txn = conn.begin()
    session = Session(bind=conn)
    yield session
    session.close()
    txn.rollback()
    conn.close()
```

### Factory Fixtures

When you need multiple instances with different configurations:

```python
@pytest.fixture
def make_user(db_session):
    """Factory fixture — create users with custom attributes."""
    created = []

    def _make_user(username="testuser", email="test@example.com", role="user"):
        user = User(username=username, email=email, role=role)
        db_session.add(user)
        db_session.flush()
        created.append(user)
        return user

    yield _make_user

    # cleanup happens via transaction rollback


async def test_admin_can_delete_user(client, make_user):
    admin = make_user(username="admin", role="admin")
    target = make_user(username="victim", role="user")

    response = await client.delete(
        f"/users/{target.id}",
        headers={"X-User-Id": str(admin.id)}
    )
    assert response.status_code == 200
```

### Fixture for Authenticated Requests

```python
@pytest.fixture
def auth_headers():
    """Factory fixture for auth headers with different roles."""
    def _headers(user_id=1, role="user"):
        # In tests, override get_current_user to trust these headers
        return {"X-User-Id": str(user_id), "X-Role": role}
    return _headers

async def test_admin_endpoint(client, auth_headers):
    response = await client.get("/admin/stats", headers=auth_headers(role="admin"))
    assert response.status_code == 200

async def test_regular_user_rejected(client, auth_headers):
    response = await client.get("/admin/stats", headers=auth_headers(role="user"))
    assert response.status_code == 403
```

---

## 9. Testing Patterns

### Pattern 1: Happy Path

Test the expected flow first. This is your baseline.

```python
async def test_create_user_success(client):
    response = await client.post("/users", json={
        "username": "alice",
        "email": "alice@example.com",
        "password": "strongpass123"
    })

    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "alice"
    assert data["email"] == "alice@example.com"
    assert "id" in data
    assert "password" not in data  # never expose passwords
```

### Pattern 2: Error Cases

Test each error path explicitly.

```python
async def test_create_user_duplicate_email(client, make_user):
    make_user(email="taken@example.com")

    response = await client.post("/users", json={
        "username": "bob",
        "email": "taken@example.com",
        "password": "pass123"
    })

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]

async def test_create_user_invalid_email(client):
    response = await client.post("/users", json={
        "username": "bob",
        "email": "not-an-email",
        "password": "pass123"
    })

    assert response.status_code == 422  # Pydantic validation

async def test_get_user_not_found(client):
    response = await client.get("/users/99999")
    assert response.status_code == 404
```

### Pattern 3: Edge Cases

These are where production bugs hide.

```python
async def test_list_users_empty(client):
    """Empty database should return empty list, not error."""
    response = await client.get("/users")
    assert response.status_code == 200
    assert response.json() == []

async def test_create_user_unicode_name(client):
    """Non-ASCII characters must be handled correctly."""
    response = await client.post("/users", json={
        "username": "utilisateur",
        "email": "user@example.com",
        "password": "pass123"
    })
    assert response.status_code == 201

async def test_pagination_beyond_results(client, make_user):
    """Requesting page 999 should return empty, not error."""
    make_user(username="only_user")
    response = await client.get("/users?page=999&limit=10")
    assert response.status_code == 200
    assert response.json() == []
```

### Pattern 4: `@pytest.mark.parametrize`

Eliminate repetitive test functions.

```python
# ❌ Without parametrize — repetitive, hard to maintain
async def test_invalid_email_no_at(client):
    response = await client.post("/users", json={"email": "invalid", ...})
    assert response.status_code == 422

async def test_invalid_email_no_domain(client):
    response = await client.post("/users", json={"email": "user@", ...})
    assert response.status_code == 422

async def test_invalid_email_empty(client):
    response = await client.post("/users", json={"email": "", ...})
    assert response.status_code == 422
```

```python
# ✅ With parametrize — concise, easy to extend
@pytest.mark.parametrize("bad_email", [
    "invalid",
    "user@",
    "",
    "@domain.com",
    "user@.com",
    "user space@example.com",
])
async def test_invalid_email_rejected(client, bad_email):
    response = await client.post("/users", json={
        "username": "bob",
        "email": bad_email,
        "password": "pass123"
    })
    assert response.status_code == 422
```

Parametrize with IDs for readable output:

```python
@pytest.mark.parametrize("method,path,status", [
    ("GET",    "/users",    200),
    ("POST",   "/users",    201),
    ("GET",    "/users/1",  200),
    ("PUT",    "/users/1",  200),
    ("DELETE", "/users/1",  204),
], ids=["list", "create", "read", "update", "delete"])
async def test_crud_status_codes(client, method, path, status, make_user):
    make_user()  # ensure user with id=1 exists
    response = await client.request(method, path, json={"username": "x", "email": "x@x.com", "password": "p"})
    assert response.status_code == status
```

### Pattern 5: Testing Response Structure

Don't just check status codes. Validate the shape of the response.

```python
async def test_user_response_structure(client, make_user):
    user = make_user(username="alice", email="alice@example.com")

    response = await client.get(f"/users/{user.id}")
    data = response.json()

    # Check required fields exist
    assert "id" in data
    assert "username" in data
    assert "email" in data
    assert "created_at" in data

    # Check sensitive fields are excluded
    assert "password" not in data
    assert "hashed_password" not in data

    # Check types
    assert isinstance(data["id"], int)
    assert isinstance(data["username"], str)
```

---

## 10. Common Mistakes

### Mistake 1: Override Leaking Between Tests

```python
# ❌ Overrides persist across tests — silent cross-contamination
def test_a():
    app.dependency_overrides[get_db] = fake_db
    ...

def test_b():
    # still using fake_db from test_a!
    ...
```

```python
# ✅ Always clean up — use autouse fixture
@pytest.fixture(autouse=True)
def cleanup_overrides():
    yield
    app.dependency_overrides.clear()
```

### Mistake 2: Shared Mutable State Between Tests

```python
# ❌ In-memory "database" shared across tests
fake_users = []

def test_create_user():
    fake_users.append({"id": 1, "name": "alice"})
    assert len(fake_users) == 1

def test_list_users_empty():
    assert len(fake_users) == 0  # FAILS — alice is still there
```

```python
# ✅ Use fixtures to create fresh state per test
@pytest.fixture
def fake_users():
    return []

def test_create_user(fake_users):
    fake_users.append({"id": 1, "name": "alice"})
    assert len(fake_users) == 1

def test_list_users_empty(fake_users):
    assert len(fake_users) == 0  # passes — fresh list
```

### Mistake 3: Sync Test Calling Async Code

```python
# ❌ This doesn't work — coroutine is never awaited
def test_async_endpoint():
    result = some_async_function()  # returns coroutine object
    assert result == expected       # comparing coroutine to value

# ✅ Make the test async
async def test_async_endpoint():
    result = await some_async_function()
    assert result == expected
```

### Mistake 4: Not Testing the Actual Endpoint

```python
# ❌ Testing the service function directly — skips middleware, auth, validation
async def test_get_user():
    user = await user_service.get(1)
    assert user.name == "alice"

# ✅ Test through the HTTP interface — catches the full stack
async def test_get_user(client, make_user):
    user = make_user(username="alice")
    response = await client.get(f"/users/{user.id}")
    assert response.status_code == 200
    assert response.json()["username"] == "alice"
```

Both are valuable, but they test different things. The first is a unit test of the service. The second is an integration test of the endpoint. You need both.

### Mistake 5: Ignoring Lifespan Events

If your app uses `lifespan` for startup/shutdown logic:

```python
# ❌ Client created without lifespan — startup hooks don't run
client = TestClient(app)  # lifespan not triggered

# ✅ Use context manager to trigger lifespan
with TestClient(app) as client:
    response = client.get("/")  # startup ran, shutdown will run
```

For async:

```python
# ✅ Async equivalent
async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.get("/")
```

### Mistake 6: Wrong Patch Target

```python
# app/routers/users.py
from app.services import user_service

@router.get("/users/{user_id}")
async def get_user(user_id: int):
    return await user_service.get(user_id)
```

```python
# ❌ Patching where it's defined — doesn't affect the import in users.py
with patch("app.services.user_service.get"):
    ...

# ✅ Patch where it's USED (imported)
with patch("app.routers.users.user_service.get"):
    ...
```

> Patch the **name as seen by the module under test**, not where the object is defined.

---

## Quick Reference: Full Test File

A realistic test file that combines all the patterns:

```python
# tests/test_users.py
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio  # apply to all tests in this module


class TestCreateUser:
    async def test_success(self, client):
        response = await client.post("/users", json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "StrongP@ss1"
        })
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "alice"
        assert "password" not in data

    async def test_duplicate_email(self, client, make_user):
        make_user(email="taken@example.com")
        response = await client.post("/users", json={
            "username": "bob",
            "email": "taken@example.com",
            "password": "StrongP@ss1"
        })
        assert response.status_code == 409

    @pytest.mark.parametrize("missing_field", ["username", "email", "password"])
    async def test_missing_required_field(self, client, missing_field):
        payload = {
            "username": "alice",
            "email": "alice@example.com",
            "password": "StrongP@ss1"
        }
        del payload[missing_field]
        response = await client.post("/users", json=payload)
        assert response.status_code == 422


class TestGetUser:
    async def test_existing_user(self, client, make_user):
        user = make_user(username="alice")
        response = await client.get(f"/users/{user.id}")
        assert response.status_code == 200
        assert response.json()["username"] == "alice"

    async def test_not_found(self, client):
        response = await client.get("/users/99999")
        assert response.status_code == 404

    async def test_invalid_id(self, client):
        response = await client.get("/users/not-a-number")
        assert response.status_code == 422


class TestListUsers:
    async def test_empty(self, client):
        response = await client.get("/users")
        assert response.status_code == 200
        assert response.json() == []

    async def test_pagination(self, client, make_user):
        for i in range(15):
            make_user(username=f"user_{i}", email=f"user{i}@example.com")

        page1 = await client.get("/users?page=1&limit=10")
        assert len(page1.json()) == 10

        page2 = await client.get("/users?page=2&limit=10")
        assert len(page2.json()) == 5
```

---

## Summary

| Concept | Key Point |
|---------|-----------|
| TestClient vs AsyncClient | Use `AsyncClient` + `ASGITransport` for async code |
| Dependency overrides | Always clean up with `autouse` fixture |
| Database testing | Transaction rollback is fastest and cleanest |
| Mocking HTTP | `respx` for httpx calls, `AsyncMock` for functions |
| Fixtures | Factory fixtures > creating objects inline |
| Parametrize | Eliminates copy-paste test duplication |
| Patch target | Patch where the name is **used**, not where it's defined |
| Test isolation | No shared mutable state, no leaked overrides |

> **The key insight**: FastAPI's dependency injection system makes testing straightforward — override dependencies with fakes, test through the HTTP interface, and clean up after every test. If testing is painful, your production code probably has a dependency injection problem.
