# 01 — The Testing Mental Model

> **Who this is for**: Backend engineers deciding what failure a test should observe and at which
> system boundary.

> **Key insight**: Choose the lowest test layer that can observe the failure being protected against.

---

## Start with the regression, not the test layer

A test is an executable risk control. Before writing one, finish this sentence:

> **This test fails if ...**

The ending must name a meaningful regression: “a duplicate delivery charges twice,” “a user can
read another tenant's invoice,” or “a committed order has no outbox event.” “This helper returns a
different value” is useful only when that value is itself a public behavior or invariant.

For a checkout service, a compact **risk-to-proof map** might look like this:

| Behavior or risk | Regression caught | Stable oracle | Cheapest faithful profile | Dependencies |
|---|---|---|---|---|
| Premium discount | The pricing rule changes | Final total is `90` | Unit | Explicit values |
| Tenant isolation | Alice reads Bob's order | HTTP `404` and no leaked fields | API slice | Fake order port, real auth policy |
| One charge per order | A retry charges twice | One provider operation and one idempotency row | Integration | Real Postgres, recording provider fake |
| Migration compatibility | A deploy cannot read an old row | Migrated row loads with preserved values | Integration | Disposable production-dialect DB |
| Provider schema | A field the adapter consumes disappears | Selected response fields decode | Live/contract job | Provider sandbox |

Choose the **oracle**—the observable fact that decides pass or fail—before choosing mocks or
fixtures. If you cannot name a stable oracle, the behavior is underspecified; copying the current
implementation into assertions will only freeze an accident.

> **Core:** Protect behavior, invariants, failure modes, compatibility surfaces, and previously
> observed bugs. Do not add a test merely because a line is uncovered.

---

## Tests reduce uncertainty; they do not prove correctness

Tests do not prove that code works for every input and environment. They catch selected regressions
and make changes safer.

Without tests:

- Every deploy is a gamble
- Refactoring is terrifying
- Bugs are discovered by users

With tests:

- You change code and know within seconds if something broke
- New team members can't silently break existing behavior
- Your CI pipeline becomes a safety net

---

## Prioritize by consequence and evidence

Not everything deserves a test. Focus on **value per test**.

| Prioritize | Examples | Why |
|----------|-------------|-----|
| Irreversible effects | Charges, email, destructive writes | A retry can repeat damage |
| Security boundaries | Auth, permissions, tenant isolation | A regression exposes data or capability |
| Durable state | Transactions, constraints, migrations | Partial state survives the request |
| Compatibility surfaces | HTTP/message schemas, package exports | Independent consumers can break |
| Recovery behavior | Retries, cancellation, redelivery, resume | The happy path cannot expose it |
| Common journeys | Create, update, reconcile | Frequent paths deserve fast feedback |

What NOT to test:

- FastAPI/Pydantic internals (they're already tested)
- Trivial pass-through endpoints with no logic
- Third-party library behavior

---

## Build a complementary proof portfolio

```
         /  E2E  \          Few — slow, brittle, expensive
        /----------\
       / Integration \      Some — test component boundaries
      /----------------\
     /    Unit Tests     \  Many — fast, isolated, cheap
    /____________________\
```

The pyramid is a cost reminder, not a required ratio. Push exhaustive decision tables to cheap
tests, then move upward only when a lower boundary cannot expose the defect. Keep a broader test
only when wiring, lifecycle, serialization, process, or real-infrastructure behavior adds distinct
evidence.

Classify a test by what it actually executes—not its directory, duration, or number of mocks:

| Profile | What really executes | Distinct confidence |
|---|---|---|
| **Unit** | One in-process behavior and explicit collaborators | Domain rules, parsing, routing, error classification |
| **Component / API slice** | In-process ASGI app; outer effects deliberately replaced | HTTP mapping, dependency wiring, auth policy, response schema |
| **Contract** | An externally consumed schema or compatibility subset | Serialization and consumer/provider expectations |
| **Integration** | Concrete adapter plus disposable real DB, broker, filesystem, or protocol endpoint | Dialect, transaction, lifecycle, encoding, delivery semantics |
| **End to end** | Deployed application across its real internal boundaries | Process configuration and a few critical journeys |
| **Live** | Shared or paid external provider | Current provider compatibility, not deterministic correctness |

An endpoint exercised through `ASGITransport` with a fake repository is an **API slice**, not an
integration test. A repository method against disposable Postgres is an **integration test**, even
if it runs in 50 ms. A sandbox call is **live**; it may also verify a contract, but it is not
hermetic.

---

## The same feature needs different proofs only when each adds evidence

The line is fuzzy in FastAPI projects because a single function can often be exercised at either level. A working definition:

| Level | Boundary | Example | Where it lives |
|-------|----------|---------|----------------|
| **Unit** | One function/class, no I/O | `validate_email("x@y.com")` returns `True` | [03](03_unit_testing.md) |
| **API slice** | ASGI app with outer effects replaced | `POST /users` maps a duplicate email to `409` | [04](04_endpoint_testing.md), [05](05_dependency_overrides.md) |
| **Integration** | Concrete repository, real DB | Concurrent inserts enforce the production unique constraint | [08](08_database_testing.md) |
| **E2E** | Deployed API and its real internal services | HTTP create → committed row → outbox publication | Small CI/deployment profile |
| **Live** | Real external provider | Stripe sandbox still returns the fields the adapter consumes | [09](09_mocking_external.md) |

Do not repeat the full branch matrix at every row. One API case can prove error mapping while unit
tests own the business matrix and database integration owns the constraint. For queues, retries,
redelivery, and crash windows, follow the dedicated [background-work failure-injection
guide](../../background_work/reliability/06_failure_injection_and_testing.md).

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

Hermetic defaults are part of repeatability: normal test commands must not need developer
credentials, shared staging state, paid models, or accidental public network. Live checks belong to
an explicit, bounded profile with separate credentials, cost, timeout, and cleanup controls.

---

## Next

- [02 — Setup](02_setup.md) — install dependencies, configure pytest, structure your test tree.
