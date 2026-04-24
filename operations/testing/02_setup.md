# 02 — Setup

> **Purpose**: Install the testing toolchain, configure pytest, and lay out a project structure that scales.

---

## Install Dependencies

```bash
pip install pytest pytest-asyncio httpx respx
```

Common additions, covered in later files:

```bash
pip install asgi-lifespan       # async lifespan startup/shutdown — see 04_endpoint_testing.md
pip install pytest-cov          # coverage — see 11_coverage_and_ci.md
pip install pytest-xdist        # parallel test runs — see 11_coverage_and_ci.md
pip install pytest-repeat pytest-randomly  # flake detection — see 11_coverage_and_ci.md
pip install hypothesis          # property-based testing — see 03_unit_testing.md
pip install syrupy              # snapshot testing — see 10_test_patterns.md
pip install testcontainers      # real-DB testing — see 08_database_testing.md
pip install moto                # AWS mocking — see 09_mocking_external.md
```

---

## Project Structure

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
│   ├── conftest.py          # shared fixtures — see 07_fixtures.md
│   ├── unit/                # fast, no I/O — see 03_unit_testing.md
│   │   ├── test_services.py
│   │   └── test_schemas.py
│   ├── integration/         # ASGI stack + DB — see 04/08
│   │   ├── test_users.py
│   │   └── test_auth.py
└── pyproject.toml
```

Separating `unit/` from `integration/` lets you run the fast suite on every save and the slow suite on every push:

```bash
pytest tests/unit              # < 1s feedback loop
pytest tests/integration       # full stack, slower
pytest                         # everything
```

---

## `pyproject.toml` Configuration

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"          # no @pytest.mark.asyncio on every test
testpaths = ["tests"]
python_files = "test_*.py"
python_classes = "Test*"
python_functions = "test_*"

# fail fast on unexpected warnings
filterwarnings = [
    "error",
    "ignore::DeprecationWarning:some_noisy_dep.*",
]

# markers — declare them to avoid "unknown marker" warnings
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "integration: requires external services",
]
```

> Setting `asyncio_mode = "auto"` treats every `async def test_*` as an async test automatically. Without it, the default `"strict"` mode requires `@pytest.mark.asyncio` on each one. See [06 — Async Testing](06_async_testing.md) for the trade-offs.

---

## Running Tests

```bash
pytest                              # all tests, default output
pytest -v                           # one line per test
pytest -x                           # stop at first failure
pytest -k "user and not slow"       # select by name
pytest -m "not integration"         # select by marker
pytest tests/unit/test_services.py::test_discount  # single test
pytest --lf                         # last failed only
pytest --ff                         # failed first, then the rest
```

---

## Sanity-Check Your Setup

Save this as `tests/test_setup.py` — if it fails, nothing else will work:

```python
import pytest


def test_sync_works():
    assert 1 + 1 == 2


async def test_async_works():
    """Requires asyncio_mode = 'auto' in pyproject.toml."""
    assert 1 + 1 == 2


def test_fixtures_work(tmp_path):
    """tmp_path is a pytest built-in — if it works, plugins are loaded."""
    (tmp_path / "x").write_text("hello")
    assert (tmp_path / "x").read_text() == "hello"
```

Run `pytest tests/test_setup.py -v`. If the async test fails with `PytestUnhandledCoroutineWarning`, your `asyncio_mode` is wrong.

---

## Next

- [03 — Unit Testing](03_unit_testing.md) — test pure logic without spinning up FastAPI.
