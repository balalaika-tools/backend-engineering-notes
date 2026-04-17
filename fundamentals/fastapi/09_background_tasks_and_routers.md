# BackgroundTasks, APIRouter, and OpenAPI Customization

> Three small but common things every FastAPI app needs. Collected here because each is too short for its own file, and they're all about *shape of the app* rather than request-handling mechanics.

---

## 1. `BackgroundTasks` — Fire-and-Forget *After* the Response

When an endpoint should respond to the client immediately but still needs to do something afterwards — send an email, emit an audit event, write a log — that's `BackgroundTasks`. The task runs **after the response is sent** to the client, in the same worker process.

```python
from fastapi import BackgroundTasks, FastAPI

app = FastAPI()


def send_welcome_email(email: str, name: str) -> None:
    # This runs AFTER the response has been returned.
    smtp_client.send(to=email, subject=f"Welcome, {name}")


@app.post("/signup")
async def signup(user: UserIn, background_tasks: BackgroundTasks):
    user_id = await create_user(user)
    background_tasks.add_task(send_welcome_email, user.email, user.name)
    return {"id": user_id}
```

The client gets `{"id": ...}` right away. The email send happens after, without blocking the response.

### When `BackgroundTasks` is the right tool

- **Small, fast, best-effort work** that belongs to this request but is not in the critical path: send an email, log to an analytics service, emit a webhook, increment a counter.
- **Local to this process.** If the worker restarts mid-task, the work is lost. No retries, no persistence.
- **Trusted to finish.** A failure in the background task is silent to the client (the response is long gone).

### When to reach for Dramatiq / a real queue instead

- Work takes more than a few seconds (blocks the worker from serving other requests if not async-friendly).
- Work must survive a process crash — e.g. charging a card, shipping an order.
- Work needs retries, scheduling, rate limiting, or backpressure.
- Work needs to be distributed across machines.

→ See [background_work/02_dramatiq.md](../../background_work/02_dramatiq.md) for that tier.

### Gotchas

- **BackgroundTasks runs in the same worker.** A long task blocks that worker from handling new requests (if the task is CPU-heavy) or consumes its event-loop time (if it's slow async work).
- **No error reporting to the client.** Log inside the task; failures do not propagate.
- **Order matters for exception safety.** If the endpoint raises after `add_task`, the task still runs (FastAPI has already enqueued it). If you want a task to run only on success, `add_task` last.
- **Use `async def` tasks for I/O, sync `def` for CPU.** Sync tasks run in the thread pool; async tasks run on the event loop.

---

## 2. `APIRouter` — Organizing a Larger App

Past a handful of endpoints, `app.get(...)` / `app.post(...)` on a single `FastAPI()` gets unwieldy. `APIRouter` lets you group related endpoints into their own module and mount them under a path prefix with a tag.

### Structure

```
app/
├── main.py              ← creates FastAPI(), includes routers
├── routers/
│   ├── __init__.py
│   ├── users.py         ← APIRouter for /users
│   ├── orders.py        ← APIRouter for /orders
│   └── admin.py         ← APIRouter for /admin
└── deps.py              ← shared dependencies
```

### `routers/users.py`

```python
from fastapi import APIRouter, Depends

from app.deps import get_current_user

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(get_current_user)],   # applied to every route in this router
    responses={404: {"description": "User not found"}},
)


@router.get("/me")              # full path: /users/me
async def me(user = Depends(get_current_user)):
    return user


@router.get("/{user_id}")       # full path: /users/{user_id}
async def get_user(user_id: int):
    ...
```

### `main.py`

```python
from fastapi import FastAPI
from app.routers import users, orders, admin

app = FastAPI(title="My API", version="1.0.0")

app.include_router(users.router)
app.include_router(orders.router)
app.include_router(admin.router, prefix="/admin")   # prefix can also be added here
```

### What `APIRouter` gives you

- **Path prefix**: all routes inherit the prefix. Change once, everywhere updates.
- **Tag grouping**: OpenAPI groups endpoints by tag in the docs UI.
- **Router-level dependencies**: an auth dependency on the router runs for every route in it, no repetition.
- **Router-level responses**: shared response schemas (e.g. common 404, 401 shapes).
- **Nested routers**: one router can `include_router` another, for deeper hierarchies.

### Sensible patterns

- **One router per domain concept** (users, orders, payments), not one per file.
- **Put the auth dependency at the router level**, not on every route — it's less error-prone.
- **Keep the router definition at module top**; don't build it conditionally.
- **Name the router variable `router`** by convention so `app.include_router(module.router)` reads the same everywhere.
- **Versioning**: mount the whole app under `/v1` either via `root_path` (if the gateway strips the prefix) or by including every router under a v1 prefix. Don't try to serve v1 and v2 from the same FastAPI instance — use two instances on two paths.

---

## 3. OpenAPI Customization Beyond `responses=`

FastAPI derives the OpenAPI schema automatically. For polish — or for endpoints you want to hide — you have a few more levers than `responses=`.

### Per-endpoint metadata

```python
@app.post(
    "/items/",
    response_model=Item,
    status_code=201,
    tags=["items"],
    summary="Create a new item",
    description="Long-form markdown description shown in the docs UI.",
    response_description="The created item",
    operation_id="create_item",                    # stable id for client codegen
    deprecated=False,
    include_in_schema=True,
)
async def create_item(item: ItemIn): ...
```

| Parameter | What it does |
|-----------|--------------|
| `summary` | One-line title in the docs UI |
| `description` | Full markdown body — supports multi-line docstrings too |
| `response_description` | Text for the primary success response |
| `operation_id` | Stable identifier used by OpenAPI clients (codegen); change it and every client SDK breaks — pick carefully |
| `tags` | OpenAPI grouping; can be per-endpoint or per-router |
| `deprecated=True` | Marks the endpoint as deprecated in the schema; browsers/clients show a warning |
| `include_in_schema=False` | Omits the endpoint from OpenAPI entirely — use for health checks, internal debug routes |

### Hiding internal endpoints

```python
@app.get("/healthz", include_in_schema=False)
async def health():
    return {"ok": True}
```

Keeps `/docs` clean and doesn't expose internal surface area to consumers of the OpenAPI spec.

### Security schemes in the OpenAPI spec

When you use `OAuth2PasswordBearer`, `HTTPBearer`, etc., the resulting OpenAPI includes a `securitySchemes` block automatically. Client codegen picks this up and generates auth-aware clients.

For customization — e.g. to document an API-key header that's not a FastAPI security dependency — you can override the OpenAPI schema directly:

```python
from fastapi.openapi.utils import get_openapi

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title="My API", version="1.0.0", routes=app.routes)
    schema["components"]["securitySchemes"] = {
        "ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
    }
    app.openapi_schema = schema
    return schema

app.openapi = custom_openapi
```

Overriding `app.openapi` is the escape hatch for anything the per-endpoint parameters can't express.

### Sorting tags in the docs UI

Tag order in `/docs` follows the order you declare them on the `FastAPI()` constructor:

```python
app = FastAPI(
    openapi_tags=[
        {"name": "users", "description": "User management"},
        {"name": "orders", "description": "Order placement and tracking"},
        {"name": "admin", "description": "Internal admin endpoints"},
    ],
)
```

Useful for public APIs where the docs order is part of the developer experience.

---

## See also

- [02_dependency_injection.md §7b Lifespan](./02_dependency_injection.md#7b-lifespan-resources-that-outlive-a-single-request) — where long-lived resources live.
- [07_error_handling.md](./07_error_handling.md) — `responses=` and exception-to-status mapping.
- [../../background_work/02_dramatiq.md](../../background_work/02_dramatiq.md) — durable background work.
