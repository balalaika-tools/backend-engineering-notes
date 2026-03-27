# FastAPI Dependency Injection

A complete guide to FastAPI's built-in dependency injection system — from mental model to production patterns.

---

## 1. What Is Dependency Injection?

FastAPI has **built-in dependency injection (DI)** that allows you to:

- Share logic across endpoints
- Manage resources (DB sessions, auth, configs)
- Keep endpoints clean and testable

Dependencies are **callables** (functions or classes) declared using `Depends`.

---

## 2. The Mental Model

### What `Depends` Actually Means

`Depends` does **not** mean "take data from the request."

`Depends` means:

> **"Call this callable and inject its return value."**

That's it. Where the data comes from depends on the callable's signature — the same parameter resolution rules apply.

### The Core Rule

> **Every function parameter — endpoint or dependency — is resolved using the same rules.**

There are no special rules for dependencies. A dependency's parameters follow the exact same mapping:

- No default → path or query
- Has default → query
- Pydantic model → body
- Explicit annotation → that source

---

## 3. Basic Dependency Injection

### Simple Function Dependency

```python
from fastapi import Depends, FastAPI

app = FastAPI()

def get_user():
    return {"username": "alice"}

@app.get("/profile")
def profile(user=Depends(get_user)):
    return user
```

FastAPI:

1. Calls `get_user()`
2. Injects its return value into `user`
3. Handles execution order automatically

### Dependency with Query Parameters

```python
def pagination_params(page: int = 1, limit: int = 20):
    return {"page": page, "limit": limit}

@app.get("/items")
def list_items(pagination=Depends(pagination_params)):
    return pagination
```

Request:

```
GET /items?page=3
```

Result:

```python
{"page": 3, "limit": 20}
```

The dependency's parameters (`page`, `limit`) are resolved as query parameters because they have defaults.

---

## 4. `Depends()` with Pydantic Models

You can use a Pydantic model as a dependency:

```python
from pydantic import BaseModel

class Pagination(BaseModel):
    page: int = 1
    limit: int = 20

@app.get("/items")
def endpoint(p: Pagination = Depends()):
    return p
```

FastAPI interprets this as:

```python
Pagination(page=?, limit=?)
```

Since the fields have defaults → query parameters.

Request:

```
GET /items?page=2
```

Result:

```python
Pagination(page=2, limit=20)
```

### Important Distinction

| Signature                  | Meaning         | Value Source   |
| -------------------------- | --------------- | -------------- |
| `x: Model`                 | Body parameter  | Request body   |
| `x: Model = Depends()`     | Call `Model()`  | Based on fields (usually query) |

The same model with `Depends()` behaves completely differently than without.

---

## 5. Dependencies with Path and Headers

Dependencies can pull from any request source:

```python
from fastapi import Header

def get_context(
    user_id: int,                    # path parameter (if in route)
    token: str = Header(...)         # header
):
    return {"user_id": user_id, "token": token}

@app.get("/users/{user_id}")
def endpoint(ctx=Depends(get_context)):
    return ctx
```

Request:

```
GET /users/42
Authorization: Bearer abc123
```

Result:

```python
{"user_id": 42, "token": "Bearer abc123"}
```

---

## 6. Dependency Hierarchies

Dependencies can depend on other dependencies.

```python
def get_db():
    return "db_connection"

def get_user(db=Depends(get_db), user_id: int = 1):
    return f"user {user_id} from {db}"

@app.get("/user")
def endpoint(user=Depends(get_user)):
    return user
```

Resolution order:

```
get_db() → get_user(db, user_id) → endpoint(user)
```

FastAPI builds a **dependency graph**, not a flat list.

---

## 7. Class-Based Dependencies

Classes work as dependencies — useful for services and configs:

```python
class Settings:
    def __init__(self):
        self.debug = True
        self.api_key = "secret"

def get_settings():
    return Settings()

@app.get("/debug")
def debug(settings=Depends(get_settings)):
    return {"debug": settings.debug}
```

Or use the class directly (FastAPI calls `__init__`):

```python
class CommonParams:
    def __init__(self, q: str = None, skip: int = 0, limit: int = 10):
        self.q = q
        self.skip = skip
        self.limit = limit

@app.get("/items")
def read_items(commons: CommonParams = Depends()):
    return commons
```

---

## 8. Database Session Pattern

The most common dependency pattern — setup and teardown with `yield`:

```python
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/users")
def get_users(db=Depends(get_db)):
    return db.query(User).all()
```

Key points:

- `yield` enables **setup + teardown**
- FastAPI guarantees cleanup (even on exceptions)
- One session per request

---

## 9. Authentication Pattern

```python
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_current_user(token: str = Depends(oauth2_scheme)):
    user = decode_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user

@app.get("/me")
def me(user=Depends(get_current_user)):
    return user
```

Authentication becomes:

- Reusable across endpoints
- Centralized logic
- Automatically enforced

---

## 10. Async Dependencies

FastAPI handles async dependencies naturally:

```python
async def get_redis():
    redis = await create_redis_pool()
    try:
        yield redis
    finally:
        await redis.close()

@app.get("/cache")
async def get_cached(redis=Depends(get_redis)):
    return await redis.get("key")
```

FastAPI ensures:

- Proper awaiting
- Correct async cleanup
- No event-loop issues

---

## 11. Dependency Caching

By default, dependencies are **cached per request**:

```python
def get_settings():
    print("Called!")  # Only prints once per request
    return Settings()

@app.get("/test")
def test(
    s1=Depends(get_settings),
    s2=Depends(get_settings)
):
    return s1 is s2  # True
```

To disable caching:

```python
Depends(get_settings, use_cache=False)
```

Useful when:

- You need fresh data each time
- The dependency is stateful

---

## 12. Global Dependencies

Apply dependencies to an entire app or router:

```python
def verify_api_key(api_key: str = Header(...)):
    if api_key != "secret":
        raise HTTPException(status_code=403)

# App-level
app = FastAPI(dependencies=[Depends(verify_api_key)])

# Router-level
router = APIRouter(dependencies=[Depends(verify_api_key)])
```

Perfect for:

- Authentication
- Rate limiting
- Request logging
- API key validation

---

## 13. Testing with Dependency Overrides

Override dependencies in tests:

```python
def fake_get_db():
    return FakeDB()

# Override
app.dependency_overrides[get_db] = fake_get_db

# Test runs with FakeDB instead of real DB

# Clean up
app.dependency_overrides.clear()
```

Benefits:

- No real database needed
- No external services
- Fast, isolated tests

---

## 14. Resolution Summary Table

| Signature                  | Meaning           | Value Source                     |
| -------------------------- | ----------------- | -------------------------------- |
| `x: Model`                 | Body parameter    | Request body                     |
| `x: Model = Depends()`     | Call `Model()`    | Based on fields (usually query)  |
| `x = Depends(fn)`          | Call `fn()`       | Based on `fn` signature          |
| `fn(x: int)`               | Path or query     | Request                          |
| `fn(x: int = 1)`           | Query             | Request                          |
| `fn(x = Header(...))`      | Header            | Request                          |
| `fn()`                     | No inputs         | Nothing                          |

---

## 15. Anti-Patterns

Avoid these common mistakes:

- Creating resources inside endpoints instead of dependencies
- Using globals instead of injected dependencies
- Putting business logic directly in routes
- Overusing dependencies for trivial values
- Not using `yield` for resources that need cleanup

---

## 16. Best Practices

- **Keep endpoints thin** — they should orchestrate, not implement
- **Move logic into dependencies or services**
- **Use `yield` for resource management** — DB sessions, connections, files
- **Depend on abstractions** — makes testing easier
- **Compose dependencies at router/app level** — don't repeat yourself
- **Use caching wisely** — understand when you need fresh instances

---

## 17. The Key Insight

> **FastAPI does not inject data — it injects the result of function calls.**

Where the data comes from depends **only** on the callable's signature. Once you understand this, dependency injection in FastAPI becomes completely predictable.

Think of dependencies as **request-scoped wiring**:

- Each request builds a dependency graph
- FastAPI resolves it top-down
- Cleanup happens automatically

You describe *what you need* — FastAPI handles *how and when* it's provided.
