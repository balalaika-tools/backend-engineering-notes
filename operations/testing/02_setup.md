# 02 — Setup

> **Who this is for**: Python backend engineers creating a pytest suite from an empty `tests/`
> directory.

## Quick proof: one test is discovered and passes

After `pip install pytest`, save this as `tests/test_setup.py`:

```python
def test_pytest_is_running():
    assert 2 + 2 == 4
```

Run `pytest -q tests/test_setup.py`. The success signal is `1 passed`; `collected 0 items` means the
file or function naming convention is wrong. The rest of this note adds async support, layout,
markers, and the commands useful once the baseline works.

> **Key insight**: A test toolchain is installed only when discovery, execution, and exit status all
> work in the same environment that will run CI.

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
pytest -m integration tests/integration  # real disposable boundaries
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
addopts = [
    "--strict-markers",
    "-m", "not integration and not e2e and not live",
]

# fail fast on unexpected warnings
filterwarnings = [
    "error",
    "ignore::DeprecationWarning:some_noisy_dep.*",
]

# markers — declare them to avoid "unknown marker" warnings
markers = [
    "integration: uses disposable real infrastructure",
    "e2e: starts or calls the deployable system",
    "live: calls a shared or paid external provider",
    "llm_eval: runs a nondeterministic dataset evaluation",
    "slow: unusually expensive within its proof profile",
]
```

> Setting `asyncio_mode = "auto"` treats every `async def test_*` as an async test automatically. Without it, the default `"strict"` mode requires `@pytest.mark.asyncio` on each one. See [06 — Async Testing](06_async_testing.md) for the trade-offs.

---

## Running Tests

Start with three commands: `pytest` for the hermetic default suite, `pytest -x` for a short feedback
loop, and `pytest path/to/test.py::test_name` while working on one failure. Select every broader
profile explicitly so its prerequisites and failures remain visible.

```bash
pytest                              # hermetic default profile
pytest -v                           # hermetic profile, one line per test
pytest -x                           # stop at first failure
pytest -k "user and not slow"       # select by name
pytest -m "not integration and not e2e and not live"  # explicit hermetic profile
pytest -m integration               # disposable DB/broker/service emulator profile
pytest -m e2e                       # deployed-process profile
pytest -m live                      # shared/provider profile; credentials required
pytest tests/unit/test_services.py::test_discount  # single test
pytest --lf                         # last failed only
pytest --ff                         # failed first, then the rest
```

Markers describe execution cost or prerequisites, not business packages. An `orders` test can be a
unit, integration, or E2E test; `@pytest.mark.orders` would not tell CI which environment it needs.
A selected infrastructure or live job must fail when its prerequisite is missing—an all-skipped
green job proves nothing.

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
