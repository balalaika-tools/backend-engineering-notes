# BackgroundTasks, APIRouter, and OpenAPI Customization

> Three small but common things every FastAPI app needs. Collected here because each is too short for its own file, and they're all about *shape of the app* rather than request-handling mechanics.

Verified against **FastAPI 0.141.1 / Starlette 1.3.1** (checked 2026-08-03).

---

## 1. `BackgroundTasks` — a callback bolted onto the response, not a queue

An endpoint should answer the client immediately but still has work to do afterwards — send a welcome email, emit an audit event, write a log line. `BackgroundTasks` is FastAPI's built-in answer, and every single one of its behaviours falls out of **one mechanism**, so learn the mechanism first.

Starlette collects the callables you register into a list, attaches that list to the `Response` object, and `Response.__call__` awaits them *after* the body bytes have gone out:

```python
# starlette/responses.py — the whole mechanism, paraphrased
await send({"type": "http.response.body", "body": self.body})
if self.background is not None:
    await self.background()      # your tasks, sequentially, in the order added
```

Three facts you will meet in production are all just that line, read carefully:

1. **No response object → no tasks.** An endpoint that raises never builds one, so nothing is ever awaited.
2. **The request's ASGI call is not finished until the tasks are.** A slow task keeps occupying the worker after the client has its answer.
3. **A task's exception is raised inside that request's ASGI call**, at a point where the client already has its `200`.

> **The near-miss**: "fire-and-forget" suggests the request is over and the task now runs independently of it. It doesn't. The response *bytes* are gone, but the ASGI call for that request is still in flight until the last task returns. That is why a task's traceback is reported against that request, and why a 30-second task holds the worker for 30 seconds after the client has moved on.

### Minimal version

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

### How you know the task actually fired

There is no client-visible difference between a task that ran, a task that raised, and a task that never existed — the client gets the same `200` in all three cases. The only place the difference shows up is the server log.

**Worked** — the task's own log line appears *after* uvicorn's access line for that request:

```
INFO:     127.0.0.1:63575 - "POST /signup HTTP/1.1" 200 OK
welcome email sent to a@b.c
```

**Didn't** — uvicorn prints `ERROR: Exception in ASGI application` with a traceback whose tail is unmistakable, and the client's `200` is unchanged:

```
INFO:     127.0.0.1:63576 - "POST /broken HTTP/1.1" 200 OK
ERROR:    Exception in ASGI application
Traceback (most recent call last):
  ...
  File ".../starlette/responses.py", line 170, in __call__
    await self.background()
  File ".../starlette/background.py", line 36, in __call__
    await task()
  ...
RuntimeError: smtp refused connection
```

⚠️ **A task that never fired at all leaves no trace anywhere** — no access-log anomaly, no error, no client signal. If you need to know whether background work happened, the task must log its own success line, and you must alert on the *absence* of it. That requirement is the first hint that you may want a real queue.

### When `BackgroundTasks` is the right tool

- **Small, fast, best-effort work** that belongs to this request but is not in the critical path: send an email, log to an analytics service, emit a webhook, increment a counter.
- **Local to this process.** If the worker restarts mid-task, the work is lost. No retries, no persistence.
- **Losable.** Nothing downstream depends on the task having run.

### When to reach for Dramatiq / a real queue instead

- Work takes more than a few seconds (it occupies the worker for that whole time).
- Work must survive a process crash — e.g. charging a card, shipping an order.
- Work needs retries, scheduling, rate limiting, or backpressure.
- Work needs to be distributed across machines.

→ See [Dramatiq](../../background_work/frameworks/dramatiq/overview.md) for that tier.

### Three ways a background task silently never runs

⚠️ **The endpoint raises after `add_task`** — including `HTTPException`. No response object is built, so nothing is awaited. `raise HTTPException(400)` after registering a task yields `400` to the client and zero task executions, with nothing logged about the dropped task. (See [fastapi#2604](https://github.com/fastapi/fastapi/issues/2604).)

⚠️ **An earlier task in the same chain raised.** Tasks on one `BackgroundTasks` object run sequentially in a plain `for` loop, and the first exception ends the chain — every task added after it is dropped. Wrap each task body in its own `try/except` if later tasks must still run:

```python
def send_welcome_email(email: str, name: str) -> None:
    try:
        smtp_client.send(to=email, subject=f"Welcome, {name}")
    except Exception:
        # Prevents one failing task from cancelling every task queued after it
        logger.exception("welcome_email_failed", extra={"email": email})
```

Reproduced on FastAPI 0.141.1 / Starlette 1.3.1: two tasks added via `add_task`, the first raising — the second never executed, the client still received `200`, and a registered `@app.exception_handler(Exception)` *was* invoked but its `500` response was discarded because the response had already started. The original exception then propagated to uvicorn.

⚠️ **The worker process died first.** Deploy, OOM kill, or `SIGKILL` between the response and the task, and the task is simply gone. Nothing retries it and nothing records that it was pending.

### Guaranteeing a task on the error path

Because the endpoint's `BackgroundTasks` object is discarded along with the aborted response, the only way to run something on an error path is to attach a *new* task to the response the exception handler builds:

```python
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

app = FastAPI()


@app.post("/signup")
async def signup(user: UserIn, request: Request):
    # Stash the arguments the handler will need — it cannot reach the endpoint's locals.
    request.state.audit = (user.email, user.name)
    ...
    raise HTTPException(status_code=409, detail="email already registered")


@app.exception_handler(HTTPException)
async def on_http_error(request: Request, exc: HTTPException):
    email, name = getattr(request.state, "audit", (None, None))
    return JSONResponse(
        {"detail": exc.detail},
        status_code=exc.status_code,
        background=BackgroundTask(record_failed_signup, email, name),
    )
```

⚠️ Two limits on this workaround. The handler must **reconstruct** the task with its own arguments — hence the `request.state` stash — and it only covers exception types you actually register a handler for. An unhandled exception on a response that has already started (the second failure mode above) runs the handler but throws its response away, so this cannot rescue a task that failed mid-chain.

### `async def` vs `def` tasks

Use **`async def` for I/O**. A sync `def` task is dispatched to anyio's default thread limiter, which has **40 tokens**; past 40 concurrent sync tasks the rest queue behind the limiter, and they compete with every other `def` handler in the app for the same 40 slots.

For genuinely CPU-bound work, use **neither** — routing CPU-bound Python through `def` does not keep the event loop free, because the worker thread still holds the GIL. Move it to a process pool or a real queue. Source: [anyio threads](https://anyio.readthedocs.io/en/stable/threads.html), checked 2026-08-03.

> **Key insight**: `BackgroundTasks` is not a queue with one worker; it is a callback bolted onto a response object. Its reliability is therefore exactly the reliability of that response getting built and sent — which is why every one of its failure modes is, underneath, a response that never happened.

---

## 2. `APIRouter` — one prefix, one tag, one auth dependency per domain

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

`get_current_user` appears twice on purpose, and the two appearances do different jobs: the **router-level** `dependencies=[...]` entry *enforces* it on every route, while the **signature** form is how a route obtains the returned value. They are not redundant, and they do not double the work — FastAPI caches dependency results per request, so the function executes exactly once per request even when declared both ways (verified: one call per request, FastAPI 0.141.1).

### `main.py`

```python
from fastapi import FastAPI
from app.routers import users, orders, admin

app = FastAPI(title="My API", version="1.0.0")

app.include_router(users.router)
app.include_router(orders.router)
app.include_router(admin.router, prefix="/admin")   # ← only if admin.py's APIRouter() has NO prefix
```

### Two things in this section that bite on day one

⚠️ **Route declaration order is load-bearing.** FastAPI matches routes in declaration order and returns the first match. Declare `@router.get("/{user_id}")` before `@router.get("/me")` and `GET /users/me` never reaches `me()` — the parameterised route matches first and path validation fails:

```json
{"detail": [{"type": "int_parsing", "loc": ["path", "user_id"],
             "msg": "Input should be a valid integer, unable to parse string as an integer",
             "input": "me"}]}
```

The `users.py` above happens to be in the right order. **Always declare literal paths before parameterised ones at the same level.**

⚠️ **The prefix belongs in exactly one of the two places.** If `admin.py` sets `prefix="/admin"` on its own `APIRouter(...)` — the way `users.py` does — *and* `main.py` passes `prefix="/admin"` to `include_router`, the two concatenate. Every `/admin/...` request then returns `404` and nothing warns you:

```python
paths -> ['/admin/admin/ping']      # from app.openapi()["paths"]
GET /admin/ping -> 404
```

The tell is the doubled path in `/docs` and in `app.openapi()["paths"]`. Pick one place — router-level is the convention, because the prefix then lives next to the routes it applies to.

⚠️ **A prefix must not end in `/`.** Both `APIRouter(prefix="/users/")` and `include_router(..., prefix="/users/")` raise at *import* time: `AssertionError: A path prefix must not end with '/', as the routes will start with '/'`. This one at least fails loudly.

### What `APIRouter` gives you

- **Path prefix**: all routes inherit the prefix. Change once, everywhere updates.
- **Tag grouping**: OpenAPI groups endpoints by tag in the docs UI.
- **Router-level dependencies**: an auth dependency on the router runs for every route in it, no repetition.
- **Router-level responses**: shared response schemas (e.g. common 404, 401 shapes).
- **Nested routers**: one router can `include_router` another, for deeper hierarchies.

### Where the prefix, the tag, and the auth dependency belong

- **One router per domain concept** (users, orders, payments), not one per file.
- **Put the auth dependency at the router level**, not on every route — it's less error-prone, and a route that needs the *value* can still add `Depends(...)` to its signature without a second execution.
- **Keep the router definition at module top**; don't build it conditionally.
- **Name the router variable `router`** by convention so `app.include_router(module.router)` reads the same everywhere.
- **Versioning**: for a single live version, mount the whole app under `/v1` either via `root_path` (if the gateway strips the prefix) or by including every router under a `/v1` prefix. To serve **two** versions at once, build a second `FastAPI()` and mount it:

  ```python
  v2_app = FastAPI(title="My API", version="2.0.0")
  v2_app.include_router(users_v2.router)

  app.mount("/v2", v2_app)
  ```

  The reason to prefer mounting over one instance with two prefixes is the schema: each mounted sub-application gets its **own independent OpenAPI document and its own `/docs`**, so a v1 client's spec never contains v2 paths. FastAPI sets the sub-app's `root_path` from the mount path automatically, so `/v2/docs` resolves without extra configuration. Source: [sub-applications](https://fastapi.tiangolo.com/advanced/sub-applications/), checked 2026-08-03.

---

## 3. OpenAPI Customization Beyond `responses=`

FastAPI derives the OpenAPI schema automatically. For polish — or for endpoints you want to hide — you have a few more levers than `responses=`.

### Per-endpoint metadata

In practice you will reach for three of these: **`summary`**, **`tags`**, and **`include_in_schema`**. The rest matter when you generate client SDKs or run a deprecation cycle.

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
| **`summary`** | One-line title in the docs UI |
| `description` | Full markdown body — supports multi-line docstrings too |
| `response_description` | Text for the primary success response |
| `operation_id` | Stable identifier used by OpenAPI clients (codegen); change it and every client SDK breaks — pick carefully |
| **`tags`** | OpenAPI grouping; can be per-endpoint or per-router |
| `deprecated=True` | Sets `deprecated: true` in the schema; Swagger UI renders the operation struck through and labelled *deprecated*, and generated clients may emit a deprecation annotation. Browsers do nothing — this is a documentation flag, not a runtime one |
| **`include_in_schema=False`** | Omits the endpoint from OpenAPI entirely — use for health checks, internal debug routes |

⚠️ **`operation_id` must be unique across the whole app.** Set the same one on two operations and FastAPI still emits the spec, with only a warning at schema-build time — `UserWarning: Duplicate Operation ID create_item for function b` — after which the spec is invalid and codegen collides on the duplicated name.

To set operation IDs app-wide instead of one endpoint at a time, pass `generate_unique_id_function` to `FastAPI(...)` or `APIRouter(...)`:

```python
from fastapi.routing import APIRoute

def readable_operation_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"

app = FastAPI(generate_unique_id_function=readable_operation_id)
```

The caveat travels with the convenience: a scheme built from `route.name` requires every path-operation *function* name to be unique within its tag, across modules. FastAPI's default generator avoids this by folding in the path and method (`create_item__items__post`), which is uglier but collision-free. Source: [path operation advanced configuration](https://fastapi.tiangolo.com/advanced/path-operation-advanced-configuration/), checked 2026-08-03.

### Hiding internal endpoints

```python
@app.get("/healthz", include_in_schema=False)
async def health():
    return {"ok": True}
```

Keeps `/docs` clean and doesn't expose internal surface area to consumers of the OpenAPI spec.

### Security schemes in the OpenAPI spec

When you use `OAuth2PasswordBearer`, `HTTPBearer`, etc., the resulting OpenAPI includes a `securitySchemes` block automatically. Client codegen picks this up and generates auth-aware clients.

For customization — e.g. to document an API-key header that's deliberately *not* a FastAPI security dependency — you can override the OpenAPI schema directly:

```python
from fastapi.openapi.utils import get_openapi

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    # setdefault, not schema["components"]: get_openapi() only emits "components"
    # when there is something to put in it, so an app with no Pydantic models and
    # no security dependencies raises KeyError: 'components' here.
    schema.setdefault("components", {})["securitySchemes"] = {
        "ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
    }
    app.openapi_schema = schema
    return schema

app.openapi = custom_openapi
```

Reading `app.title` / `app.version` rather than re-typing `"My API"` / `"1.0.0"` keeps the override from drifting away from the `FastAPI(title=..., version=...)` values.

**How you know it worked:**

```bash
curl -s localhost:8000/openapi.json | jq .components.securitySchemes
# {"ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
```

and an **Authorize** button appears in `/docs`.

⚠️ **The silent failure is the cache.** `custom_openapi()` returns early on a populated `app.openapi_schema`. If anything called `app.openapi()` or hit `/docs` *before* `app.openapi = custom_openapi` was assigned — a startup log line that dumps the spec, a test module imported first, a router registered later — the un-customised schema is already cached and is served from then on. No error is raised anywhere; `jq` just returns `null`. Assign the override immediately after creating routes and before anything reads the schema.

⚠️ **Injecting `securitySchemes` is documentation only.** Nothing in the app now validates `X-API-Key` as a result, and `/docs` will render an Authorize box for a scheme that is not enforced. Enforcement is a separate dependency (`APIKeyHeader` + a `Depends`); if you stop after this section, the endpoint is documented as protected and is not protected.

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
- [Dramatiq](../../background_work/frameworks/dramatiq/overview.md) — broker-backed worker execution.
- [Background Work](../../background_work/README.md) — durable tasks, stateful workflows, and architecture choices.
