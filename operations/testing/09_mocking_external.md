# 09 — Mocking External Services

> **Who this is for**: Backend engineers who need deterministic tests around HTTP, cloud SDK, queue, or other external boundaries.

> **Purpose**: Intercept external calls (HTTP, AWS, queues) in tests without hitting the real service. Know which tool belongs in which layer.

> **Key insight**: Mock the first interface you do not own, then use a separate contract test to verify that your model of that interface still matches reality.

Your endpoint calls an external API. You don't want tests to:

- Hit real APIs (slow, flaky, costs money).
- Depend on network availability.
- Mutate real data.

You have four tools, each operating at a different layer.

---

## Decision Matrix

| Tool | Best For | Intercepts At | Scope |
|------|----------|---------------|-------|
| `respx` | httpx HTTP calls | Transport layer | Any httpx client |
| `AsyncMock` / `Mock` | Any async/sync function | Function call | Targeted replacement |
| `patch` | Module-level objects, globals | Import path | The name under test |
| `moto` | AWS SDK calls | boto3 internals | All AWS services |
| Dependency override | FastAPI dependencies | DI container | The overridden dep |

> **Prefer dependency overrides** over `patch` when testing FastAPI endpoints. They're explicit, don't rely on import paths, and match how FastAPI actually resolves dependencies. See [05](05_dependency_overrides.md). Reach for `patch` when the dependency isn't wired through `Depends`.

---

## `respx` — Best for httpx-Based Code

`respx` intercepts httpx requests at the transport level. No monkey-patching, no import games.

```bash
pip install respx
```

```python
import respx
import httpx
import pytest
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
        return_value=Response(200, json={"temp": 15, "city": "london"}),
    )

    result = await fetch_weather("london")
    assert result["temp"] == 15
    assert result["city"] == "london"
```

### Testing Error Scenarios

```python
@respx.mock
async def test_weather_api_timeout():
    respx.get("https://api.weather.com/london").mock(
        side_effect=httpx.ConnectTimeout("Connection timed out"),
    )

    with pytest.raises(httpx.ConnectTimeout):
        await fetch_weather("london")


@respx.mock
async def test_weather_api_500():
    respx.get("https://api.weather.com/london").mock(
        return_value=Response(500, json={"error": "internal"}),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_weather("london")
```

### Verifying the Call

```python
@respx.mock
async def test_calls_upstream_with_auth():
    route = respx.post("https://api.example.com/orders").mock(
        return_value=Response(201, json={"id": "abc"}),
    )
    await create_order({"item": "x"})

    assert route.called
    assert route.call_count == 1
    sent = route.calls.last.request
    assert sent.headers["Authorization"].startswith("Bearer ")
    assert "item" in sent.content.decode()
```

---

## `AsyncMock` — For Any Async Callable

```python
from unittest.mock import AsyncMock


# app/services.py
class PaymentService:
    async def charge(self, user_id: int, amount: float) -> dict:
        # calls Stripe API
        ...


async def checkout(service: PaymentService, user_id: int, amount: float) -> str:
    result = await service.charge(user_id=user_id, amount=amount)
    return f"receipt:{result['id']}"


# tests/test_payments.py
async def test_charge_user():
    mock_service = AsyncMock()
    mock_service.charge.return_value = {"status": "success", "id": "ch_123"}

    result = await checkout(mock_service, user_id=1, amount=99.99)

    assert result == "receipt:ch_123"
    mock_service.charge.assert_called_once_with(user_id=1, amount=99.99)
```

The test now exercises application-owned behavior—the receipt mapping—while the mock stands in for
the first external boundary. Calling the mock directly would prove only that `AsyncMock` returned
the value the test itself configured.

Use `AsyncMock` when you are injecting the mock directly — as a constructor argument, a fixture, or a `Depends` override. It is cleaner than `patch` because there is no import path to get wrong.

---

## `patch` — Replacing Module-Level Objects

```python
from unittest.mock import patch, AsyncMock


async def test_endpoint_with_mocked_service(client):
    with patch(
        "app.routers.users.payment_service.charge",
        new_callable=AsyncMock,
    ) as mock_charge:
        mock_charge.return_value = {"status": "success"}

        response = await client.post("/pay", json={"amount": 50})

        assert response.status_code == 200
        mock_charge.assert_called_once()
```

### Get the Patch Target Right

```python
# app/routers/users.py
from app.services import user_service

@router.get("/users/{user_id}")
async def get_user(user_id: int):
    return await user_service.get(user_id)
```

`from ... import user_service` binds that object to a name in `app.routers.users`. At call time the
route resolves its own module's name, so changing only `app.services.user_service` may leave the
bound reference untouched.

```python
# ❌ This may replace a different name from the one the route resolves
with patch("app.services.user_service.get"):
    ...

# ✅ Replace the name in the module under test
with patch("app.routers.users.user_service.get"):
    ...
```

> **Patch the name as seen by the module under test**, not where the object is defined. The top debugging expense in mock-heavy suites.

---

## `moto` — Mock AWS Services

Intercepts `boto3`/`botocore` calls in-memory. No real AWS account touched.

> **`moto` does not natively intercept `aioboto3`/`aiobotocore`** (async clients). For async AWS code, either run moto in [server mode](https://docs.getmoto.org/en/latest/docs/server_mode.html) and point your client at it, or use a helper like `aiomoto`/`pytest-aiomoto`. The in-process `@mock_aws` decorator below works only for synchronous `boto3`.

```bash
pip install moto
```

```python
from moto import mock_aws
import boto3


@mock_aws
def test_s3_upload():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket")
    s3.put_object(Bucket="test-bucket", Key="hello.txt", Body=b"hi")

    obj = s3.get_object(Bucket="test-bucket", Key="hello.txt")
    assert obj["Body"].read() == b"hi"
```

moto v5+ unified every service-specific decorator (`@mock_s3`, `@mock_dynamodb`, ...) into the single `@mock_aws`. If you see `@mock_s3` in existing code, it's pre-v5 — the fix is a one-line import change.

### As a pytest Fixture

```python
import pytest
from moto import mock_aws


@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def s3_bucket(aws):
    import boto3
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket")
    return "test-bucket"


def test_upload(s3_bucket):
    # all AWS calls are mocked inside this test
    ...
```

---

## Live Provider Compatibility Checks

Mocks guarantee that your code does the right thing **given your understanding of the upstream**. They catch nothing when the upstream changes their API.

For critical dependencies, run a bounded **live** test against the upstream's sandbox environment.
It can verify a compatibility contract, but classify it as live because it uses credentials,
network, shared state, and the provider's current behavior. Keep it separate from the hermetic
suite:

```python
# tests/live/test_stripe_compatibility.py
import os

import pytest

pytestmark = pytest.mark.live


@pytest.fixture
def stripe_sandbox_key():
    key = os.getenv("STRIPE_SANDBOX_KEY")
    if not key:
        pytest.fail("live profile selected without STRIPE_SANDBOX_KEY")
    return key


async def test_charge_returns_fields_consumed_by_adapter(stripe_sandbox_key):
    result = await stripe.charge_test_payment(stripe_sandbox_key, amount_cents=100)
    # assert the shape you depend on
    assert "id" in result
    assert "status" in result
```

Run `pytest -m live tests/live` in a separate, time- and spend-bounded CI job on a schedule. The job
must fail if its secret or sandbox is unavailable; silently skipping every selected test produces a
green check with no evidence. Redact recordings and failure artifacts.

---

## Layered Tests for the Same Endpoint

A useful pattern is to prove the same feature at complementary boundaries without repeating its
whole branch matrix:

| Level | Client uses | Catches |
|-------|-------------|---------|
| Application unit | Action + injected provider fake | Business decisions and error classification |
| API slice | In-process ASGI app + real action + `respx` transport | HTTP mapping, DI, auth, request construction |
| Local contract | Serializer/schema fixture | Fields and message shapes consumers rely on |
| Live compatibility | Real adapter + real upstream sandbox | Current upstream API drift |

Keep the exhaustive matrix at the cheapest owner. The broader rows stay only when they prove
different wiring, serialization, or current-provider behavior.

---

## Don't Mock What You Own

Mocking your own services turns tests into "I call `svc.foo()` and it returns what I told it to". That tests nothing.

Mock things **at the edge of your system** — external APIs, AWS, the database if you must, the message queue — and use real objects for your own code. If you can't build your own services cheaply in tests, that's a signal the services are too coupled, not that you need more mocks.

---

## Next

- [10 — Test Patterns](10_test_patterns.md) — parametrize, snapshots, and the realistic end-to-end example.
