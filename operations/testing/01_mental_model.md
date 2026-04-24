# 01 — The Testing Mental Model

> **Purpose**: Before any pytest fixtures or `TestClient` code, establish *what* to test, *why*, and *at which level*. Every later file assumes this vocabulary.

---

## Why Test?

Tests are not about proving code works. Tests are about **catching regressions** and **enabling fearless refactoring**.

Without tests:

- Every deploy is a gamble
- Refactoring is terrifying
- Bugs are discovered by users

With tests:

- You change code and know within seconds if something broke
- New team members can't silently break existing behavior
- Your CI pipeline becomes a safety net

---

## What to Test

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

---

## The Test Pyramid

```
         /  E2E  \          Few — slow, brittle, expensive
        /----------\
       / Integration \      Some — test component boundaries
      /----------------\
     /    Unit Tests     \  Many — fast, isolated, cheap
    /____________________\
```

For FastAPI backends:

- **Unit tests** — pure functions, validators, business logic, no framework involvement. See [03 — Unit Testing](03_unit_testing.md).
- **Integration tests** — endpoints with test DB and dependency overrides. See [04 — Endpoint Testing](04_endpoint_testing.md).
- **E2E tests** — full stack with external services (usually in CI only, nightly).

> **Rule of thumb**: If a test needs network access or takes > 1 second, it's integration, not unit.

---

## Unit vs Integration — A Concrete Distinction

The line is fuzzy in FastAPI projects because a single function can often be exercised at either level. A working definition:

| Level | Boundary | Example | Where it lives |
|-------|----------|---------|----------------|
| **Unit** | One function/class, no I/O | `validate_email("x@y.com")` returns `True` | [03](03_unit_testing.md) |
| **Integration** | Multiple components, real DB, ASGI stack | `POST /users` returns 201 and row appears in DB | [04](04_endpoint_testing.md), [08](08_database_testing.md) |
| **E2E** | Full system, real upstreams | Browser → API → DB → Stripe sandbox | Out of scope — nightly only |

Both unit and integration are valuable. They catch *different* bugs. A unit test catches "this function has the wrong branch"; an integration test catches "this function is wired up wrong" or "the migration renamed a column".

---

## Test Doubles — The Taxonomy

When you can't use the real thing, you substitute it. The four common substitutes, from least to most behavior:

| Double | What it does | Example |
|--------|-------------|---------|
| **Dummy** | Placeholder, never used | An unused `user` arg passed to satisfy a signature |
| **Stub** | Returns hard-coded values | `get_weather()` always returns `{"temp": 15}` |
| **Fake** | Working but simplified implementation | In-memory dict standing in for a database |
| **Mock** | Verifies interactions | Asserts `charge()` was called with specific args |

**Common mistake:** using "mock" to mean all of the above. The distinction matters — mocks can make tests brittle (they assert *how* something was called, not just *what* came back). Prefer stubs and fakes for most cases; reach for mocks when the interaction itself is the contract under test.

Covered in practical detail in [03 — Unit Testing](03_unit_testing.md) and [09 — Mocking External Services](09_mocking_external.md).

---

## The AAA Pattern

Structure every test as three blocks:

```python
def test_user_discount_applied():
    # Arrange — set up inputs
    user = make_user(tier="premium")
    cart = Cart(items=[Item(price=100)])

    # Act — perform the operation under test
    total = checkout(user, cart)

    # Assert — verify the outcome
    assert total == 90  # 10% premium discount
```

A test that mixes arrange/act/assert lines ("`assert create_user(...)["id"] == 1`") hides intent. Separating them makes failures self-diagnosing.

---

## FIRST Principles

A good test is:

- **Fast** — runs in milliseconds, not seconds
- **Isolated** — order-independent, no shared mutable state
- **Repeatable** — same result every run, no time/network dependencies
- **Self-validating** — passes or fails, no manual log inspection
- **Timely** — written with the code, not weeks later

Tests that violate these principles are technical debt — they get disabled, skipped, or ignored when they go red.

---

## Next

- [02 — Setup](02_setup.md) — install dependencies, configure pytest, structure your test tree.
