# 10 — Test Patterns

> **Purpose**: Patterns that apply across every test you write — happy path, errors, edge cases, parametrization, snapshot testing, and how to structure files that don't rot.

---

## Pattern 1: Happy Path First

Write the expected flow first. This is your baseline — it proves the plumbing works before you go hunting edge cases.

```python
async def test_create_user_success(client):
    response = await client.post("/users", json={
        "username": "alice",
        "email": "alice@example.com",
        "password": "correct_horse_battery_staple",
    })

    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "alice"
    assert data["email"] == "alice@example.com"
    assert "id" in data
    assert "password" not in data          # never expose passwords
    assert "hashed_password" not in data   # not that either
```

---

## Pattern 2: Error Cases

Test each error path explicitly. Error branches are where users actually live.

```python
async def test_create_user_duplicate_email(client, make_user):
    make_user(email="taken@example.com")

    response = await client.post("/users", json={
        "username": "bob",
        "email": "taken@example.com",
        "password": "correct_horse_battery_staple",
    })

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


async def test_create_user_invalid_email(client):
    response = await client.post("/users", json={
        "username": "bob",
        "email": "not-an-email",
        "password": "correct_horse_battery_staple",
    })
    assert response.status_code == 422  # Pydantic validation


async def test_get_user_not_found(client):
    response = await client.get("/users/99999")
    assert response.status_code == 404
```

---

## Pattern 3: Edge Cases

Edge cases are where production bugs hide. Make a habit of asking: "what if the list is empty? what if the ID is the max int? what if someone sends unicode?"

```python
async def test_list_users_empty(client):
    """Empty database should return empty list, not error."""
    response = await client.get("/users")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_user_unicode_name(client):
    """Non-ASCII characters must be handled correctly."""
    response = await client.post("/users", json={
        "username": "utilisateur",
        "email": "user@example.com",
        "password": "correct_horse_battery_staple",
    })
    assert response.status_code == 201


async def test_pagination_beyond_results(client, make_user):
    """Requesting page 999 should return empty, not error."""
    make_user(username="only_user")
    response = await client.get("/users?page=999&limit=10")
    assert response.status_code == 200
    assert response.json() == []
```

---

## Pattern 4: `@pytest.mark.parametrize`

Eliminate copy-paste test functions. One test body, many inputs.

```python
# ❌ Without parametrize — repetitive, hard to maintain
async def test_invalid_email_no_at(client):
    response = await client.post("/users", json={"email": "invalid", ...})
    assert response.status_code == 422

async def test_invalid_email_no_domain(client):
    response = await client.post("/users", json={"email": "user@", ...})
    assert response.status_code == 422

async def test_invalid_email_empty(client):
    response = await client.post("/users", json={"email": "", ...})
    assert response.status_code == 422
```

```python
# ✅ With parametrize — concise, easy to extend
@pytest.mark.parametrize("bad_email", [
    "invalid",
    "user@",
    "",
    "@domain.com",
    "user@.com",
    "user space@example.com",
])
async def test_invalid_email_rejected(client, bad_email):
    response = await client.post("/users", json={
        "username": "bob",
        "email": bad_email,
        "password": "correct_horse_battery_staple",
    })
    assert response.status_code == 422
```

### Parametrize with IDs for readable output

```python
@pytest.mark.parametrize("method,path,status", [
    ("GET",    "/users",    200),
    ("POST",   "/users",    201),
    ("GET",    "/users/1",  200),
    ("PUT",    "/users/1",  200),
    ("DELETE", "/users/1",  204),
], ids=["list", "create", "read", "update", "delete"])
async def test_crud_status_codes(client, method, path, status, make_user):
    make_user()  # ensure user with id=1 exists
    response = await client.request(
        method, path,
        json={"username": "x", "email": "x@x.com", "password": "correct_horse_battery_staple"},
    )
    assert response.status_code == status
```

Running `pytest -v` now prints `test_crud_status_codes[list]`, `test_crud_status_codes[create]` — far more useful than `[GET-/users-200]`.

---

## Pattern 5: Test the Response Structure

Don't just check status codes. Validate the shape of the response — the API contract lives in the response body.

```python
async def test_user_response_structure(client, make_user):
    user = make_user(username="alice", email="alice@example.com")

    response = await client.get(f"/users/{user.id}")
    data = response.json()

    # Required fields
    assert {"id", "username", "email", "created_at"} <= data.keys()

    # Sensitive fields must be excluded
    assert "password" not in data
    assert "hashed_password" not in data

    # Types
    assert isinstance(data["id"], int)
    assert isinstance(data["username"], str)
```

For larger contracts, snapshot testing (below) scales better.

---

## Snapshot / Approval Testing

When your API contract is stable and you want to detect accidental schema changes, snapshot testing is a fast, mechanical check.

```bash
pip install syrupy
```

```python
def test_get_user_shape(client, snapshot):
    resp = client.get("/users/42")
    assert resp.status_code == 200
    assert resp.json() == snapshot
```

First run stores the response in `__snapshots__/`. Subsequent runs compare. Any diff fails the test; `pytest --snapshot-update` blesses the new shape.

Snapshot tests catch things unit tests don't: accidental field renames, extra fields leaking out, ordering changes. Pair with OpenAPI schema export for a second layer — dump `app.openapi()` into a file, commit it, diff in CI on every change.

### Excluding Volatile Fields

Don't snapshot timestamps, auto-IDs, or UUIDs — they change every run. Use `syrupy`'s path matchers to blank them:

```python
from syrupy.matchers import path_type


def test_user_shape_stable(client, snapshot):
    resp = client.get("/users/42")
    assert resp.json() == snapshot(
        matcher=path_type({"id": (int,), "created_at": (str,)}),
    )
```

---

## Class-Based Test Organization

Group tests for the same resource or flow in a class. Keeps file navigation sane and lets related tests share a fixture state:

```python
class TestCreateUser:
    async def test_success(self, client):
        ...

    async def test_duplicate_email(self, client, make_user):
        ...

    @pytest.mark.parametrize("missing_field", ["username", "email", "password"])
    async def test_missing_required_field(self, client, missing_field):
        ...


class TestGetUser:
    async def test_existing_user(self, client, make_user):
        ...

    async def test_not_found(self, client):
        ...


class TestListUsers:
    async def test_empty(self, client):
        ...

    async def test_pagination(self, client, make_user):
        ...
```

Pytest has no special support for classes — it's just a grouping convention. Don't use `setUp/tearDown`; use fixtures.

---

## Realistic End-to-End Example

A full test file combining the patterns:

```python
# tests/integration/test_users.py
import pytest
from httpx import AsyncClient


class TestCreateUser:
    async def test_success(self, client):
        response = await client.post("/users", json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "correct_horse_battery_staple",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "alice"
        assert "password" not in data

    async def test_duplicate_email(self, client, make_user):
        make_user(email="taken@example.com")
        response = await client.post("/users", json={
            "username": "bob",
            "email": "taken@example.com",
            "password": "correct_horse_battery_staple",
        })
        assert response.status_code == 409

    @pytest.mark.parametrize("missing_field", ["username", "email", "password"])
    async def test_missing_required_field(self, client, missing_field):
        payload = {
            "username": "alice",
            "email": "alice@example.com",
            "password": "correct_horse_battery_staple",
        }
        del payload[missing_field]
        response = await client.post("/users", json=payload)
        assert response.status_code == 422


class TestGetUser:
    async def test_existing_user(self, client, make_user):
        user = make_user(username="alice")
        response = await client.get(f"/users/{user.id}")
        assert response.status_code == 200
        assert response.json()["username"] == "alice"

    async def test_not_found(self, client):
        response = await client.get("/users/99999")
        assert response.status_code == 404

    async def test_invalid_id(self, client):
        response = await client.get("/users/not-a-number")
        assert response.status_code == 422


class TestListUsers:
    async def test_empty(self, client):
        response = await client.get("/users")
        assert response.status_code == 200
        assert response.json() == []

    async def test_pagination(self, client, make_user):
        for i in range(15):
            make_user(username=f"user_{i}", email=f"user{i}@example.com")

        page1 = await client.get("/users?page=1&limit=10")
        assert len(page1.json()) == 10

        page2 = await client.get("/users?page=2&limit=10")
        assert len(page2.json()) == 5
```

---

## Testing for *Behavior*, Not Implementation

Tests that assert internal state or call counts break on every refactor. Tests that assert outcomes survive.

```python
# ❌ Brittle — breaks if the repo adds caching
def test_creates_user_once(service, repo_mock):
    service.register("alice", "a@b.com")
    assert repo_mock.create.call_count == 1

# ✅ Durable — tests the user-visible outcome
def test_creates_user(service):
    service.register("alice", "a@b.com")
    assert service.find_by_username("alice") is not None
```

Mocks are sometimes necessary (see [09](09_mocking_external.md)), but their default posture should be "verify output, not calls".

---

## Next

- [11 — Coverage & CI](11_coverage_and_ci.md) — measure what's tested and wire the suite into a pipeline.
