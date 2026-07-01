# 03 — Unit Testing

> **Purpose**: Test pure logic in isolation — no ASGI, no DB, no network. The fastest feedback loop you have.

A unit test exercises one function or class with its dependencies faked out. It runs in milliseconds, fails deterministically, and tells you *exactly* where a regression lives. If every test in your suite goes through `TestClient`, you do not have unit tests — you have slow integration tests.

---

## What to Unit-Test

| Target | Why it's worth a unit test |
|--------|----------------------------|
| Pure functions (discount calc, slug builder) | No I/O, trivial to pin down |
| Pydantic validators | Validation rules are business rules |
| Service methods | Core logic lives here |
| Parsers / serializers | Edge cases dominate |
| Domain models | Invariants matter |
| Prompt builders | LLM behavior is fuzzy, but the prompt you send is deterministic |

What is **not** worth a unit test at this level:

- Endpoints — they have HTTP semantics; test through the ASGI stack in [04](04_endpoint_testing.md).
- Thin pass-through services that just delegate.
- SQLAlchemy query construction — test against a real DB in [08](08_database_testing.md).

---

## Testing a Pure Function

```python
# app/pricing.py
from typing import Optional

def apply_discount(price: float, tier: Optional[str]) -> float:
    if tier == "premium":
        return price * 0.9
    if tier == "enterprise":
        return price * 0.8
    return price
```

```python
# tests/unit/test_pricing.py
from app.pricing import apply_discount


def test_no_tier_returns_full_price():
    assert apply_discount(100, None) == 100


def test_premium_gets_10_percent_off():
    assert apply_discount(100, "premium") == 90


def test_enterprise_gets_20_percent_off():
    assert apply_discount(100, "enterprise") == 80


def test_unknown_tier_returns_full_price():
    assert apply_discount(100, "mystery") == 100
```

No fixtures, no mocks. Pure-function tests are the best kind: when they go red, the bug is exactly where they point.

---

## Testing Pydantic Models

Pydantic validators enforce contracts. Test them directly — do not route through an endpoint just to check validation.

```python
# app/schemas.py
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: EmailStr
    password: str = Field(min_length=12)
    referral_code: Optional[str] = None

    @field_validator("username")
    @classmethod
    def username_must_be_alphanumeric(cls, v: str) -> str:
        if not v.replace("_", "").isalnum():
            raise ValueError("username must be alphanumeric (underscores allowed)")
        return v
```

```python
# tests/unit/test_schemas.py
import pytest
from pydantic import ValidationError
from app.schemas import UserCreate


def test_valid_user():
    user = UserCreate(
        username="alice",
        email="alice@example.com",
        password="correct_horse_battery_staple",
    )
    assert user.username == "alice"
    assert user.referral_code is None  # optional default


@pytest.mark.parametrize("bad_username", ["ab", "a" * 33, "hi!", "has space"])
def test_username_validation_rejects(bad_username):
    with pytest.raises(ValidationError):
        UserCreate(
            username=bad_username,
            email="x@y.com",
            password="correct_horse_battery_staple",
        )


def test_short_password_rejected():
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(username="alice", email="a@b.com", password="short")
    # assert the specific error, not just "something failed"
    assert any("at least 12" in str(e) for e in exc_info.value.errors())
```

> **Why check the error message?** A test that only asserts `ValidationError` passes even if the wrong field failed for the wrong reason. Assert the specific failure mode so refactors don't silently break coverage.

---

## Test Doubles in Practice

From [01 — Mental Model](01_mental_model.md): dummy, stub, fake, mock. Here they are in code.

### Stub — hard-coded return values

```python
from app.services import Notifier


class StubNotifier:
    def send(self, user_id: int, message: str) -> bool:
        return True  # pretend it worked


def test_signup_sends_welcome():
    notifier = StubNotifier()
    signup(user_data, notifier)
    # assertion is about signup's result, not notifier's behavior
```

### Fake — simplified working implementation

```python
class FakeUserRepo:
    """In-memory stand-in for UserRepo — supports the same methods."""
    def __init__(self):
        self._users: dict[int, dict] = {}
        self._next_id = 1

    def create(self, username: str, email: str) -> dict:
        user = {"id": self._next_id, "username": username, "email": email}
        self._users[self._next_id] = user
        self._next_id += 1
        return user

    def get(self, user_id: int) -> Optional[dict]:
        return self._users.get(user_id)


def test_service_creates_then_reads():
    repo = FakeUserRepo()
    service = UserService(repo)

    created = service.register("alice", "a@b.com")
    fetched = service.find_by_id(created["id"])

    assert fetched == created
```

Fakes are usually the right default for dependencies your code talks to heavily. They let you test *behavior* (create-then-read round-trip) instead of *calls* (was `repo.create` invoked with X).

### Mock — verify the interaction

```python
from unittest.mock import Mock


def test_charge_failure_logs_incident():
    logger = Mock()
    service = PaymentService(logger=logger)

    with pytest.raises(PaymentDeclined):
        service.charge(user_id=1, amount=100, _force_fail=True)

    logger.error.assert_called_once()
    args, kwargs = logger.error.call_args
    assert "declined" in args[0].lower()
```

Use mocks when the interaction *is* the contract. Logging, metrics, and audit trails are good candidates. Use them sparingly elsewhere — over-mocked tests pass even when the code is broken, because they assert calls instead of outcomes.

---

## `unittest.mock` — The Essentials

```python
from unittest.mock import Mock, MagicMock, AsyncMock, patch
```

| Class | Use for |
|-------|---------|
| `Mock` | Sync callables, attribute access |
| `MagicMock` | Same, but also supports dunders (`__len__`, `__iter__`, etc.) |
| `AsyncMock` | Async callables — `await mock()` returns `return_value` |
| `patch` | Temporarily replace an object at its import path |

### Configuring return values

```python
m = Mock()
m.return_value = 42
m()                # 42

m.side_effect = [1, 2, 3]
m(), m(), m()      # 1, 2, 3

m.side_effect = ValueError("boom")
m()                # raises ValueError

# For async:
am = AsyncMock(return_value={"ok": True})
await am()         # {"ok": True}
```

### `patch` — get the target right

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

> **The rule:** patch the name as seen by the module under test, not where the object is defined. This bites everyone at least once.

---

## Testing Exceptions

```python
import pytest


def test_divide_by_zero_raises():
    with pytest.raises(ZeroDivisionError):
        1 / 0


def test_custom_exception_carries_context():
    with pytest.raises(UserNotFound) as exc_info:
        service.find_by_id(999)
    assert exc_info.value.user_id == 999
    assert "999" in str(exc_info.value)


def test_wrapped_exception_chain():
    with pytest.raises(ServiceError) as exc_info:
        service.call_flaky_dep()
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
```

---

## Property-Based Testing with Hypothesis

For code that handles wide input domains (parsers, math, serializers), example-based tests miss edge cases you did not imagine. Hypothesis generates them for you.

```bash
pip install hypothesis
```

```python
from hypothesis import given, strategies as st


@given(st.text(min_size=1, max_size=100))
def test_slugify_is_idempotent(text):
    """slugify(slugify(x)) == slugify(x) for any string."""
    once = slugify(text)
    twice = slugify(once)
    assert once == twice


@given(
    st.integers(min_value=0),
    st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
)
def test_apply_discount_never_negative(price_int, discount_pct):
    result = apply_percentage_discount(price_int, discount_pct)
    assert result >= 0  # a non-negative price can never discount below zero
```

When Hypothesis finds a failing input, it **shrinks** to the simplest example (`""`, `0`, `None`) and saves it for future runs. That shrinking is the feature — it turns "parser crashes on some 3000-character Unicode string" into "parser crashes on `\x00`".

Worth reaching for when:

- The function has an obvious invariant (`f(f(x)) == f(x)`, `encode(decode(x)) == x`, "output is always sorted").
- Inputs are structured data you can describe with strategies.

Not worth it for endpoints, I/O, or anything where the state space is small and enumerable.

---

## What Unit Tests Should Not Do

- **Call real databases.** Use a fake repo or move the test to [08](08_database_testing.md).
- **Hit the network.** Stub the client or move to [09](09_mocking_external.md).
- **Boot FastAPI.** That's endpoint testing — see [04](04_endpoint_testing.md).
- **Share mutable state.** See [12 — Common Mistakes](12_common_mistakes.md#mistake-2-shared-mutable-state-between-tests).

If a "unit" test is slow, flaky, or order-dependent, it is mislabelled. Move it to the integration suite or fix the isolation.

---

## Next

- [04 — Endpoint Testing](04_endpoint_testing.md) — climb one level up the pyramid to HTTP-level tests.
- [13 — Testing LLM Code](13_testing_llm_code.md) — apply the same unit-test discipline to prompts, adapters, schemas, and agents.
