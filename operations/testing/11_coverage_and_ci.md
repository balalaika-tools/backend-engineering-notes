# 11 — Coverage & CI

> **Who this is for**: Python teams using coverage and continuous integration to find unobserved
> paths without confusing execution with confidence.

> **Key insight**: Coverage locates unobserved execution paths; it cannot establish whether
> assertions protect the right behavior.

Coverage is a proxy metric — 100% coverage does not mean "well tested", it means "every line was executed at least once". Low coverage, though, is a reliable red flag: a file at 20% almost certainly has broken paths no one has seen fail.

---

## `pytest-cov` — The Basics

```bash
pip install pytest-cov
```

```bash
pytest --cov=app                          # coverage for the `app` package
pytest --cov=app --cov-report=term        # summary in the terminal
pytest --cov=app --cov-report=html        # open htmlcov/index.html
pytest --cov=app --cov-report=xml         # for CI upload (Codecov etc.)
pytest --cov=app --cov-fail-under=80      # fail the build below 80%
```

Configure defaults in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "--cov=app --cov-report=term-missing --cov-fail-under=80"

[tool.coverage.run]
branch = true               # count branch coverage, not just line
source = ["app"]
omit = [
    "app/migrations/*",     # alembic versions — not meaningful to cover
    "app/__main__.py",
    "*/tests/*",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
    "if __name__ == .__main__.:",
    "\\.\\.\\.",             # ellipsis in Protocol/abstract stubs
]
```

`branch = true` is important: without it, `if x: do()` counts as "covered" the moment `do()` runs once, even though the `else` branch was never exercised.

---

## Reading the Report

```
Name                       Stmts   Miss Branch BrPart  Cover   Missing
----------------------------------------------------------------------
app/services/users.py         45      3     12      2    92%   34-36, 48->52
app/routers/admin.py          22     10      6      1    56%   18-27, 31->exit
```

- `Stmts` — executable statements.
- `Miss` — statements never run.
- `BrPart` — branches partially covered (one side taken, not both).
- `Missing` — line numbers; `48->52` means the branch from line 48 to line 52 was never taken.

Focus on `BrPart` columns, not overall percentage. A file at 95% with several partial branches hides more bugs than a file at 85% with all branches fully covered.

---

## What to Exclude

Coverage should reflect code you *want* tested. Exclude:

- Generated code (Alembic migrations, protobuf stubs).
- `if __name__ == "__main__":` guards.
- Abstract methods / Protocols (`...`).
- Debug helpers guarded by `if DEBUG:`.
- `TYPE_CHECKING` imports.

Use `# pragma: no cover` for a single line you deliberately don't test:

```python
def main():  # pragma: no cover
    import uvicorn
    uvicorn.run(app)
```

Don't use pragmas to hit a percentage target. They're for genuinely un-testable code, not a workaround for not writing the test.

---

## Setting a Target

There is no universal “good” threshold. A repository may use a coverage floor to catch a test job
that stopped collecting files or to prevent large unexamined drops, but `80%` does not imply that
the important behaviors are protected. Start from the current measured baseline, inspect uncovered
high-risk branches, and ratchet only when the added tests have meaningful oracles.

Do not hide low-value production glue with `omit` merely to improve the percentage. Reserve
`omit` for generated or intentionally non-executable files. Either accept the lower threshold or
cover the glue through a higher-level test whose assertions protect the behavior that matters.

For compact high-risk deterministic logic—authorization decisions, money calculations, retry
classification—**mutation testing** can reveal a test that executes a branch without detecting a
changed outcome. Use it as a diagnostic when the project already supports it; inspect surviving
mutations semantically rather than replacing coverage-score chasing with mutation-score chasing.

---

## Parallel Test Runs — `pytest-xdist`

Once the suite passes 30 seconds, run it in parallel.

```bash
pip install pytest-xdist
pytest -n auto                 # one worker per CPU
pytest -n 4                    # exactly 4
pytest -n auto --dist loadfile # group tests by file
```

**Caveats:**

- Tests must be isolated. Shared DBs, shared temp dirs, shared `app.dependency_overrides` — all break.
- Session-scoped fixtures run **per worker**, not once total. A testcontainer (see [08](08_database_testing.md)) spun up per worker is fine; one per process is expensive.
- Combine with `--cov` using `pytest-cov`'s `--cov-append` or `COVERAGE_RUN_PARALLEL=1` + `coverage combine` in CI.

---

## GitHub Actions — Minimal CI

```yaml
# .github/workflows/test.yml
name: tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: postgres
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v6

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - run: pip install -e ".[dev]"

      - name: Run tests
        run: pytest -n auto --cov=app --cov-report=xml --cov-fail-under=80

      - name: Run disposable Postgres integration profile
        env:
          DATABASE_URL: postgresql+asyncpg://postgres:postgres@localhost:5432/postgres
        run: pytest -m integration

      - uses: codecov/codecov-action@v5
        with:
          files: coverage.xml
          token: ${{ secrets.CODECOV_TOKEN }}  # required for private repos (and existing orgs)
```

Key choices:

- **Two explicit selections** — the hermetic suite and disposable-infrastructure suite report which boundary failed.
- **Postgres as a service** — gives the integration profile the production SQL dialect.
- **`pip cache` via `setup-python`** — avoids re-downloading on every run.
- **`-n auto`** — uses all the runner's CPUs.
- **`--cov-fail-under`** — CI goes red below the threshold.

---

## Pre-Commit — Fast Feedback Before Push

Run the fast suite (and linters) locally before code leaves the dev machine:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: pytest-unit
        name: pytest (unit only)
        entry: pytest tests/unit -x
        language: system
        types: [python]
        pass_filenames: false

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.0          # pin a real version; `pre-commit autoupdate` bumps it
    hooks:
      - id: ruff-check     # formerly `ruff`; `ruff-check` is the current hook id
      - id: ruff-format
```

Unit tests under 1 second stay in the commit hook. Anything slower belongs in CI.

---

## Flaky Test Detection

If a test sometimes fails, it will. Don't ignore it — find out why.

```bash
pip install pytest-repeat pytest-randomly
pytest --count=20 tests/integration/test_thing.py  # via pytest-repeat
pytest --randomly-seed=1234 tests/integration     # reproduce or vary test order
```

Flakes almost always trace to one of:

- Shared state between tests (global dict, module-level list).
- Time-sensitive assertions (`asyncio.sleep(0.1); assert finished` under CI load).
- Nondeterministic iteration order (dicts/sets where order matters).
- Leaked dependency overrides — see [05](05_dependency_overrides.md).

Use repeats and randomized order to reproduce the failure, not to make a red test eventually pass.
A rerun plugin can gather evidence but is not a repair. Replace timer-based coordination with an
event, barrier, task group, database lock, or a bounded poll for the semantic condition; join or
cancel every spawned task during teardown.

---

## Test Speed Budget

A rule of thumb that keeps suites usable:

| Suite | Target time |
|-------|------------|
| Unit tests | < 2 s for the whole suite |
| Integration tests | < 30 s |
| Full CI run | < 5 min |

If you blow past these, the suite stops being a feedback tool and becomes a chore. Fix it — usually by marking slow tests and excluding them from the default run:

```python
@pytest.mark.slow
def test_full_cron_cycle():
    ...
```

```bash
pytest -m "not slow"           # dev default
pytest                         # CI default
```

---

## Next

- [12 — Common Mistakes](12_common_mistakes.md) — the recurring bugs that bite every pytest project eventually.
