# Testing

> **Purpose**: Production-grade testing for FastAPI applications — from the mental model to coverage and CI.

[![pytest](https://img.shields.io/badge/pytest-8.x-0A9EDC.svg?logo=pytest&logoColor=white)](https://pytest.org)
[![pytest-asyncio](https://img.shields.io/badge/pytest--asyncio-1.x-0A9EDC.svg)](https://pytest-asyncio.readthedocs.io)
[![httpx](https://img.shields.io/badge/HTTPX-0.27+-009688.svg)](https://www.python-httpx.org)
[![asgi-lifespan](https://img.shields.io/badge/asgi--lifespan-2.x-4B8BBE.svg)](https://pypi.org/project/asgi-lifespan/)
[![respx](https://img.shields.io/badge/respx-0.21+-4B8BBE.svg)](https://lundberg.github.io/respx/)
[![testcontainers](https://img.shields.io/badge/testcontainers-4.x-0DB7ED.svg)](https://testcontainers-python.readthedocs.io)
[![moto](https://img.shields.io/badge/moto-5.x-FF9900.svg)](https://docs.getmoto.org/)
[![coverage](https://img.shields.io/badge/coverage.py-7.x-2496ED.svg)](https://coverage.readthedocs.io)

---

## Prerequisites

- [FastAPI Dependency Injection](../../fundamentals/fastapi/02_dependency_injection.md) — `Depends` pattern is the foundation of testable FastAPI code.
- [Async Tutorial](../../fundamentals/concurrency/async/01_event_loop_and_tasks.md) — event loop basics, helpful for [06](06_async_testing.md).
- Basic pytest familiarity (fixtures, assertions, running a suite).

---

## Guide Structure

| Part | Topic | Description |
|------|-------|-------------|
| [01](01_mental_model.md) | Mental Model | Why test, what to test, test pyramid, test doubles taxonomy, AAA pattern |
| [02](02_setup.md) | Setup | Install, project layout, `pyproject.toml`, running tests |
| [03](03_unit_testing.md) | Unit Testing | Pure functions, Pydantic validators, test doubles, `unittest.mock`, Hypothesis |
| [04](04_endpoint_testing.md) | Endpoint Testing | `TestClient` vs `AsyncClient`, `ASGITransport`, lifespan events, WebSockets |
| [05](05_dependency_overrides.md) | Dependency Overrides | The DI override mechanism, auth bypass, cleanup patterns |
| [06](06_async_testing.md) | Async Testing | `pytest-asyncio` modes, async fixtures, event-loop scope, sync/async pitfalls |
| [07](07_fixtures.md) | Fixture Patterns | `conftest.py` hierarchy, scopes, factory fixtures, parameterized fixtures |
| [08](08_database_testing.md) | Database Testing | Transaction rollback, `testcontainers`, `pytest-postgresql`, migration checks |
| [09](09_mocking_external.md) | Mocking External Services | `respx`, `AsyncMock`, `patch`, `moto`, contract testing |
| [10](10_test_patterns.md) | Test Patterns | Happy path, errors, edge cases, `parametrize`, snapshot testing |
| [11](11_coverage_and_ci.md) | Coverage & CI | `pytest-cov`, branch coverage, `pytest-xdist`, GitHub Actions |
| [12](12_common_mistakes.md) | Common Mistakes | The recurring pitfalls every pytest project hits |
| [13](13_testing_llm_code.md) | Testing LLM Code | Prompt builders, model adapters, schemas, agent tools, evals |

---

## Reading Paths

### New to Testing

1. [01 — Mental Model](01_mental_model.md) — vocabulary and the pyramid
2. [02 — Setup](02_setup.md) — get pytest running
3. [03 — Unit Testing](03_unit_testing.md) — fastest feedback loop first
4. [04 — Endpoint Testing](04_endpoint_testing.md) — climb to HTTP-level tests
5. [12 — Common Mistakes](12_common_mistakes.md) — avoid the top pitfalls

### Testing a Real FastAPI Service

1. Skim 01–02
2. [05 — Dependency Overrides](05_dependency_overrides.md) — the DI testing idiom
3. [07 — Fixtures](07_fixtures.md) — share setup cleanly
4. [08 — Database Testing](08_database_testing.md) — real Postgres, rolled back per test
5. [09 — Mocking External Services](09_mocking_external.md) — when the thing you call is not a DB
6. [10 — Test Patterns](10_test_patterns.md) — parametrize, snapshots, realistic examples

### Testing LLM / Agent Features

1. [03 — Unit Testing](03_unit_testing.md) — pure prompt builders and service logic
2. [09 — Mocking External Services](09_mocking_external.md) — provider adapters and network boundaries
3. [13 — Testing LLM Code](13_testing_llm_code.md) — structured outputs, agent tools, and evals
4. [11 — Coverage & CI](11_coverage_and_ci.md) — keep live evals out of the fast suite

### Setting Up CI From Scratch

1. [02 — Setup](02_setup.md) — `pyproject.toml`
2. [11 — Coverage & CI](11_coverage_and_ci.md) — GitHub Actions, thresholds, flake detection

---

## Quick Start — A Minimal Working Test

```python
# tests/test_users.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def reset_overrides():
    yield
    app.dependency_overrides.clear()


async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
```

With `asyncio_mode = "auto"` in `pyproject.toml` ([02](02_setup.md)), that's the whole setup for HTTP-level tests.

If the app depends on startup/shutdown lifespan work, wrap the async client with `asgi-lifespan`'s `LifespanManager`; see [04](04_endpoint_testing.md#lifespan-events).

---

## Key Principles

- **Unit, integration, E2E serve different purposes.** Most tests should be unit. See [01](01_mental_model.md).
- **FastAPI's DI system makes testing easy.** Override dependencies with fakes. See [05](05_dependency_overrides.md).
- **Clean up shared state.** Override leaks and module-level mutables ruin suites. See [12](12_common_mistakes.md).
- **Patch where imported, not where defined.** See [03](03_unit_testing.md#patch-get-the-target-right).
- **Prefer real DB tests over mocked queries.** See [08](08_database_testing.md).
- **Mock at the edge, not inside.** See [09](09_mocking_external.md).
- **For LLMs, assert contracts instead of prose.** See [13](13_testing_llm_code.md).
- **Coverage is a proxy, not a target.** See [11](11_coverage_and_ci.md).

---

**Start with [Part 1: Mental Model →](01_mental_model.md)**
