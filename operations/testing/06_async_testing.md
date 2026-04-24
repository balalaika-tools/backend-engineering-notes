# 06 — Async Testing

> **Purpose**: Run `async def test_*` functions through pytest-asyncio without fighting the event loop. Understand the sync/async pitfalls that bite everyone.

---

## Configuring pytest-asyncio

`pytest-asyncio` defaults to **strict** mode — it only handles tests marked `@pytest.mark.asyncio`. In an async-first codebase that means decorating every test. The **auto** mode treats every `async def test_*` as an async test.

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

With auto mode:

```python
# No decorator needed — pytest-asyncio detects async automatically
async def test_async_endpoint(client):
    response = await client.get("/async-endpoint")
    assert response.status_code == 200
```

Without auto mode (default strict):

```python
import pytest

@pytest.mark.asyncio
async def test_async_endpoint(client):
    response = await client.get("/async-endpoint")
    assert response.status_code == 200
```

> **Auto mode is usually the right default for asyncio-only FastAPI projects.** Strict mode is useful when your suite mixes async frameworks and you want the marker/plugin boundary to be explicit.

---

## Async Fixtures

In auto mode, `async def` fixtures are picked up as async automatically:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_get_users(client):
    response = await client.get("/users")
    assert response.status_code == 200
```

If your app depends on `lifespan` startup/shutdown, wrap the client fixture with `asgi-lifespan`'s `LifespanManager`; the HTTPX `ASGITransport` does not send lifespan events on its own. See [04 — Endpoint Testing](04_endpoint_testing.md#lifespan-events).

In strict mode you must use `@pytest_asyncio.fixture` (note: the `pytest_asyncio` namespace, not `pytest`):

```python
import pytest_asyncio

@pytest_asyncio.fixture
async def client():
    ...
```

If you see `TypeError: object AsyncGenerator can't be used in 'await' expression`, you are in strict mode with an undecorated async fixture.

---

## Event-Loop Scope

By default, pytest-asyncio runs async tests in a function-scoped event loop. Async fixture loop scope is configurable; set it explicitly for expensive higher-scoped fixtures so future plugin defaults do not surprise you:

```python
import pytest_asyncio


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    engine = create_async_engine(TEST_DB_URL)
    yield engine
    await engine.dispose()
```

Use this when an async fixture (DB engine, test container) must survive across tests. Keep ordinary per-test clients and DB sessions function-scoped. If most async fixtures in the suite really should share a session loop, `asyncio_default_fixture_loop_scope = "session"` is also available in `pyproject.toml`, but make that an intentional suite-wide choice. See [08 — Database Testing](08_database_testing.md) for concrete patterns.

---

## Common Pitfall: Mixing Sync and Async

```python
# ❌ Wrong — sync test calling async fixture
def test_something(client):  # client is a coroutine, not a client
    response = client.get("/users")  # TypeError

# ✅ Correct — async test with async fixture
async def test_something(client):
    response = await client.get("/users")
```

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

> **Symptom to watch for:** your test passes but you see a `RuntimeWarning: coroutine ... was never awaited` in pytest output. That warning means the test body never actually ran — it created a coroutine and threw it away. Treat any `coroutine was never awaited` warning as a test bug.

---

## Testing Timeouts and Cancellation

When your code uses `asyncio.timeout`, the test should still assert the right exception type — it is `TimeoutError` (built-in) in Python 3.11+, not `asyncio.TimeoutError` (which is now an alias).

```python
import asyncio
import pytest


async def test_slow_endpoint_times_out():
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await asyncio.sleep(1)
```

---

## Testing Background Tasks

FastAPI's `BackgroundTasks` runs after the response is sent. In tests, `TestClient` blocks until those tasks complete, so you can assert their side effects:

```python
from fastapi import BackgroundTasks

@router.post("/signup")
async def signup(bg: BackgroundTasks):
    bg.add_task(send_welcome_email, user_id=1)
    return {"ok": True}


async def test_signup_queues_email(client, mocker):
    spy = mocker.patch("app.routers.signup.send_welcome_email")
    response = await client.post("/signup")
    assert response.status_code == 200
    spy.assert_called_once_with(user_id=1)
```

For Dramatiq or other task queues, do not rely on the real broker — use its in-memory test broker. Covered briefly in the Dramatiq guides (see the [background_work](../../background_work/README.md) notes).

---

## `anyio` Backends

If your app uses `anyio` rather than raw asyncio, consider AnyIO's built-in pytest plugin instead. It supports both asyncio and trio backends in the same suite:

```python
import pytest

@pytest.mark.anyio
async def test_on_both_backends():
    ...

@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request):
    return request.param
```

Most FastAPI projects stay on asyncio. Do not enable both pytest-asyncio auto mode and AnyIO auto mode in the same suite; use explicit `@pytest.mark.anyio` tests when the two plugins coexist.

---

## Debugging Flaky Async Tests

A test that sometimes passes and sometimes fails is usually one of:

- **Shared event loop state** — an unclosed session, a pending task not awaited.
- **Missing `await`** — silently skipped work (look for `coroutine was never awaited`).
- **Timer-based assertions** — `asyncio.sleep(0.1)` then asserting "it finished in 100ms" under CI load.
- **Task-order dependence** — two `asyncio.gather`'d coroutines with implicit ordering.

Re-run with `pytest-randomly` using several seeds to see if order matters. If so, the bug is in test isolation, not in the code under test.

---

## Next

- [07 — Fixtures](07_fixtures.md) — shared state across tests, factory patterns, scope selection.
