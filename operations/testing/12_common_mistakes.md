# 12 — Common Mistakes

> **Purpose**: The recurring pitfalls every FastAPI test suite hits sooner or later. Each has a minimal bad example and the fix.

---

## Mistake 1: Override Leaking Between Tests

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
# ✅ Always clean up — use an autouse fixture
@pytest.fixture(autouse=True)
def cleanup_overrides():
    yield
    app.dependency_overrides.clear()
```

See [05 — Dependency Overrides](05_dependency_overrides.md) for the full cleanup strategy.

---

## Mistake 2: Shared Mutable State Between Tests

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

Anything at module level is shared. If your test suite can't handle being run in a different order, install `pytest-randomly` and run with varied seeds until the leak shows itself.

---

## Mistake 3: Sync Test Calling Async Code

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

Watch for `RuntimeWarning: coroutine 'xyz' was never awaited` in pytest output — that warning means the test body ran but the async call was discarded. See [06 — Async Testing](06_async_testing.md).

---

## Mistake 4: Only Testing the Happy Path Through the Endpoint

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

Both are valuable, but they test different things. The first is a unit test of the service ([03](03_unit_testing.md)). The second is an integration test of the endpoint ([04](04_endpoint_testing.md)). You need both.

---

## Mistake 5: Ignoring Lifespan Events

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
from asgi_lifespan import LifespanManager

# ❌ AsyncClient + ASGITransport alone does not send lifespan events
async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.get("/")  # startup did not run

# ✅ Wrap async HTTPX tests with LifespanManager
async with LifespanManager(app) as manager:
    transport = ASGITransport(app=manager.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
```

**Symptom:** tests fail with `AttributeError` on `app.state.something`. See [04 — Endpoint Testing](04_endpoint_testing.md#lifespan-events).

---

## Mistake 6: Wrong Patch Target

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

> Patch the **name as seen by the module under test**, not where the object is defined. See [03 — Unit Testing](03_unit_testing.md#patch-get-the-target-right).

---

## Mistake 7: Testing Implementation Instead of Behavior

```python
# ❌ Brittle — breaks on every refactor
def test_creates_user(service, repo_mock):
    service.register("alice", "a@b.com")
    repo_mock.insert.assert_called_once_with(username="alice", email="a@b.com")

# ✅ Durable — asserts the user-visible outcome
def test_creates_user(service):
    user_id = service.register("alice", "a@b.com")
    fetched = service.find_by_id(user_id)
    assert fetched.username == "alice"
```

Mocks that verify "how" are useful in narrow cases (logging, metrics). As the default they produce tests that pass for bugs and fail for refactors — the exact inverse of what you want.

---

## Mistake 8: Over-Mocking Your Own Code

```python
# ❌ Mocking internal services — the test asserts what you told it
async def test_handler(mocker):
    mocker.patch("app.services.users.UserService.get", return_value={"id": 1})
    mocker.patch("app.services.billing.BillingService.charge", return_value={"ok": True})
    result = await handler(...)
    assert result == {"ok": True}  # tests nothing real
```

Mock at the **edge of your system** (external APIs, AWS, message queues). For your own services, use real instances — with a test DB ([08](08_database_testing.md)) or in-memory fakes ([03](03_unit_testing.md)). If your own services are too expensive to instantiate in tests, that's a design smell, not a reason to mock.

---

## Mistake 9: Not Resetting Env Vars

```python
# ❌ Leaks — the env var stays set for all later tests
def test_with_feature_flag():
    os.environ["FEATURE_FOO"] = "1"
    ...

# ✅ monkeypatch auto-reverses
def test_with_feature_flag(monkeypatch):
    monkeypatch.setenv("FEATURE_FOO", "1")
    ...
```

Same rule for `sys.path`, `sys.modules`, attribute mutations. Use `monkeypatch` so pytest handles cleanup.

---

## Mistake 10: Catching the Wrong Exception

```python
# ❌ Too broad — passes even if a different error fires
with pytest.raises(Exception):
    do_thing()

# ✅ Specific — test the contract you care about
with pytest.raises(UserNotFound) as exc_info:
    do_thing()
assert exc_info.value.user_id == 42
```

A test asserting `Exception` gives you no signal when the code goes wrong in a new way. Name the exact exception.

---

## Mistake 11: Skipping Instead of Fixing

```python
# ❌ Disabled because it's flaky
@pytest.mark.skip("flaky on CI")
def test_thing():
    ...
```

A skipped test is worse than no test — it tricks you into thinking coverage exists. If a test is flaky, either fix the isolation (almost always the real bug) or delete the test. Don't leave it half-alive.

Acceptable exceptions:

- `@pytest.mark.skipif(condition, reason=...)` for platform-specific or env-specific tests.
- Real environmental gates (`@pytest.mark.skipif(not has_docker())`).

Never a plain `@pytest.mark.skip` without a path to re-enabling.

---

## Mistake 12: Database Tests Without Rollback

```python
# ❌ Real inserts, no cleanup — later tests see stale data
def test_create(db):
    db.add(User(email="a@b.com"))
    db.commit()
    assert db.query(User).count() == 1  # passes the first run
    # ...then fails or cross-contaminates
```

```python
# ✅ Every test runs in a transaction that's rolled back
@pytest.fixture
def db(engine):
    conn = engine.connect()
    tx = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    tx.rollback()
    conn.close()
```

See [08 — Database Testing](08_database_testing.md#strategy-2-transaction-rollback-fast-isolated) for the full version.

---

## Back to [README](README.md)
