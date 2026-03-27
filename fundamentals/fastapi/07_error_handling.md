# FastAPI Error Handling

A complete guide to error response patterns in FastAPI — from mental model to production-grade consistency.

---

## 1. The Mental Model

### Why Error Handling Matters

Your API's error responses are part of its **contract**. Clients depend on:

- Predictable error shapes
- Correct HTTP status codes
- Actionable error messages

If your success responses are structured but your errors are ad-hoc strings, you have **half an API**.

### The Core Principle

> **Every error response should be as intentional and well-structured as every success response.**

Errors are not afterthoughts. They are first-class responses your clients will parse, log, and display.

### What "Good" Error Handling Looks Like

| Concern | Question | Mechanism |
|---------|----------|-----------|
| **Status code** | What category of problem? | HTTP status code |
| **Error code** | What specific problem? | Application-level code |
| **Message** | What happened? | Human-readable string |
| **Details** | What exactly went wrong? | Structured data (field errors, context) |
| **Consistency** | Same shape every time? | Shared error model |

---

## 2. HTTPException — The Basics

FastAPI provides `HTTPException` for returning error responses.

### Basic Usage

```python
from fastapi import FastAPI, HTTPException

app = FastAPI()

@app.get("/users/{user_id}")
def get_user(user_id: int):
    user = db.find_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
```

Response:

```json
{"detail": "User not found"}
```

### Detail as a Dict

`detail` can be a string or any JSON-serializable structure:

```python
raise HTTPException(
    status_code=404,
    detail={
        "code": "USER_NOT_FOUND",
        "message": "User not found",
        "params": {"user_id": user_id},
    },
)
```

Response:

```json
{
    "detail": {
        "code": "USER_NOT_FOUND",
        "message": "User not found",
        "params": {"user_id": 42}
    }
}
```

### Custom Headers

```python
raise HTTPException(
    status_code=401,
    detail="Invalid or expired token",
    headers={"WWW-Authenticate": "Bearer"},
)
```

Useful for auth flows (OAuth2 requires `WWW-Authenticate` on 401).

---

## 3. Custom Exception Classes

### The Problem with Raw HTTPException

Using `HTTPException` directly in business logic **couples your domain to HTTP**:

```python
# ❌ Domain logic knows about HTTP
class UserService:
    def get_user(self, user_id: int) -> User:
        user = self.repo.find(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user
```

This makes your service untestable outside of FastAPI and leaks transport concerns into business logic.

### Domain Exceptions

Define exceptions that describe **what went wrong**, not how to respond:

```python
# ✅ Domain exceptions — no HTTP knowledge
class AppException(Exception):
    """Base for all application exceptions."""
    def __init__(self, message: str, code: str | None = None):
        self.message = message
        self.code = code or self.__class__.__name__
        super().__init__(self.message)


class NotFoundError(AppException):
    """Resource does not exist."""
    pass


class ConflictError(AppException):
    """Operation conflicts with current state."""
    pass


class AuthenticationError(AppException):
    """Caller identity could not be verified."""
    pass


class AuthorizationError(AppException):
    """Caller lacks permission for this action."""
    pass


class ValidationError(AppException):
    """Input fails business-rule validation."""
    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message, code="VALIDATION_ERROR")
        self.details = details or []


class RateLimitError(AppException):
    """Caller has exceeded allowed request rate."""
    pass


class ExternalServiceError(AppException):
    """A downstream dependency failed."""
    def __init__(self, message: str, service: str):
        super().__init__(message, code="EXTERNAL_SERVICE_ERROR")
        self.service = service
```

### Clean Service Layer

```python
# ✅ Service raises domain exceptions
class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    def get_user(self, user_id: int) -> User:
        user = self.repo.find(user_id)
        if not user:
            raise NotFoundError(f"User {user_id} not found")
        return user

    def create_user(self, email: str) -> User:
        if self.repo.find_by_email(email):
            raise ConflictError(f"Email {email} is already registered")
        return self.repo.create(email=email)
```

The service knows nothing about HTTP. It can be used from CLI, background jobs, or tests.

---

## 4. Consistent Error Response Model

### The Problem

Without a standard shape, clients see:

```json
{"detail": "Not found"}
{"error": "something went wrong"}
{"message": "bad request", "errors": [...]}
{"detail": [{"loc": [...], "msg": "..."}]}
```

Four different shapes from one API. This forces clients to write special-case parsing.

### Standard Error Schema

Define a Pydantic model that **every** error response uses:

```python
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []

    model_config = {"json_schema_extra": {
        "examples": [
            {
                "code": "USER_NOT_FOUND",
                "message": "User 42 not found",
                "details": [],
            }
        ]
    }}
```

Every error response looks like:

```json
{
    "code": "USER_NOT_FOUND",
    "message": "User 42 not found",
    "details": []
}
```

Validation errors look like:

```json
{
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
        {"field": "email", "message": "Invalid email format"},
        {"field": "age", "message": "Must be >= 0"}
    ]
}
```

### Document It in OpenAPI

```python
from fastapi import FastAPI

app = FastAPI(
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Not found"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
```

Now your OpenAPI docs show the **actual** error shape, not the default `{"detail": "string"}`.

---

## 5. Global Exception Handlers

### Mapping Domain Exceptions to HTTP Responses

This is where you connect domain exceptions to HTTP. The mapping lives in **one place**.

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


# --- Mapping table ---

EXCEPTION_STATUS_MAP: dict[type[AppException], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    AuthenticationError: 401,
    AuthorizationError: 403,
    ValidationError: 422,
    RateLimitError: 429,
    ExternalServiceError: 502,
}


def _error_response(status_code: int, exc: AppException) -> JSONResponse:
    body = ErrorResponse(
        code=exc.code,
        message=exc.message,
        details=getattr(exc, "details", []),
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
    )


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    status_code = EXCEPTION_STATUS_MAP.get(type(exc), 500)
    return _error_response(status_code, exc)
```

Now **any** domain exception raised anywhere in the call stack — endpoint, dependency, service — becomes a properly formatted HTTP response.

### Catching Unhandled Exceptions

Never let raw Python tracebacks reach the client:

```python
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    import structlog
    logger = structlog.get_logger()

    logger.error(
        "unhandled_exception",
        exc_type=type(exc).__name__,
        exc_message=str(exc),
        path=request.url.path,
        method=request.method,
    )

    body = ErrorResponse(
        code="INTERNAL_ERROR",
        message="An unexpected error occurred",
    )
    return JSONResponse(status_code=500, content=body.model_dump())
```

**Critical**: The client gets a generic message. The log gets the real error. Never expose internals.

---

## 6. Validation Error Customization

### The Default Problem

FastAPI returns Pydantic validation errors in its own format:

```json
{
    "detail": [
        {
            "type": "missing",
            "loc": ["body", "email"],
            "msg": "Field required",
            "input": {},
            "url": "https://errors.pydantic.dev/2.6/v/missing"
        }
    ]
}
```

This shape differs from every other error your API returns. Clients now need two parsers.

### Override RequestValidationError

```python
from fastapi.exceptions import RequestValidationError


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    details = []
    for error in exc.errors():
        # loc is a tuple like ("body", "email") or ("query", "page")
        loc = error.get("loc", ())
        # Skip the source prefix (body, query, path, header)
        field = ".".join(str(part) for part in loc[1:]) if len(loc) > 1 else str(loc[0])
        details.append(
            ErrorDetail(field=field, message=error["msg"])
        )

    body = ErrorResponse(
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details=details,
    )
    return JSONResponse(status_code=422, content=body.model_dump())
```

Now validation errors match your standard shape:

```json
{
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
        {"field": "email", "message": "Field required"},
        {"field": "age", "message": "Input should be a valid integer"}
    ]
}
```

### Override Starlette's HTTPException Too

If you also use Starlette's `HTTPException` (from middleware or other code), override it for consistency:

```python
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    body = ErrorResponse(
        code=f"HTTP_{exc.status_code}",
        message=str(exc.detail),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=body.model_dump(),
        headers=getattr(exc, "headers", None),
    )
```

---

## 7. Exception Handler Ordering

### How FastAPI Resolves Handlers

FastAPI checks exception handlers from **most specific to least specific** (by class hierarchy).

```python
@app.exception_handler(NotFoundError)       # checked first for NotFoundError
async def handle_not_found(...): ...

@app.exception_handler(AppException)        # checked for any other AppException
async def handle_app_exception(...): ...

@app.exception_handler(Exception)           # fallback for everything else
async def handle_generic(...): ...
```

When a `NotFoundError` is raised:

1. FastAPI checks: Is there a handler for `NotFoundError`? Yes -> use it.
2. If not, it walks up the MRO: `AppException` -> `Exception`.

### The Rule

> **Register specific handlers before general ones. More specific exception types get priority.**

### Practical Setup

```python
# Specific overrides (when you need special behavior for one type)
@app.exception_handler(RateLimitError)
async def rate_limit_handler(request: Request, exc: RateLimitError):
    body = ErrorResponse(code=exc.code, message=exc.message)
    return JSONResponse(
        status_code=429,
        content=body.model_dump(),
        headers={"Retry-After": "60"},
    )


# General domain exception handler (covers everything else)
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    status_code = EXCEPTION_STATUS_MAP.get(type(exc), 500)
    return _error_response(status_code, exc)


# Absolute fallback
@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    ...
```

### Common Pitfall

```python
# ❌ This handler catches everything — specific handlers never fire
@app.exception_handler(Exception)
async def catch_all(request, exc):
    return JSONResponse(status_code=500, content={"error": "fail"})

@app.exception_handler(NotFoundError)  # Never reached if Exception catches first
async def handle_not_found(request, exc):
    ...
```

FastAPI actually resolves this correctly (specific first), but if you manually catch `Exception` in your endpoint, you swallow domain exceptions before they reach handlers.

```python
# ❌ Swallows domain exceptions — handlers never fire
@app.get("/users/{user_id}")
async def get_user(user_id: int, service: UserService = Depends(get_service)):
    try:
        return service.get_user(user_id)
    except Exception:
        raise HTTPException(status_code=500, detail="Something went wrong")
```

```python
# ✅ Let domain exceptions propagate to handlers
@app.get("/users/{user_id}")
async def get_user(user_id: int, service: UserService = Depends(get_service)):
    return service.get_user(user_id)
```

---

## 8. Logging in Exception Handlers

### Why Log in Handlers

Exception handlers are the **ideal place** to log errors because:

- They have access to the `Request` object
- They run for every error, guaranteed
- They centralize logging instead of scattering it

### Structured Logging with Request Context

```python
import structlog
import uuid

logger = structlog.get_logger()


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    status_code = EXCEPTION_STATUS_MAP.get(type(exc), 500)

    # Log at appropriate level
    log_method = logger.warning if status_code < 500 else logger.error
    log_method(
        "app_exception",
        exc_type=type(exc).__name__,
        exc_code=exc.code,
        exc_message=exc.message,
        status_code=status_code,
        path=request.url.path,
        method=request.method,
        query_params=str(request.query_params),
        client_ip=request.client.host if request.client else None,
        request_id=request.headers.get("X-Request-ID"),
    )

    return _error_response(status_code, exc)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Generate a correlation ID so ops can find this in logs
    error_id = str(uuid.uuid4())

    logger.error(
        "unhandled_exception",
        error_id=error_id,
        exc_type=type(exc).__name__,
        exc_message=str(exc),
        path=request.url.path,
        method=request.method,
        client_ip=request.client.host if request.client else None,
        request_id=request.headers.get("X-Request-ID"),
        exc_info=True,  # includes traceback in structured log
    )

    body = ErrorResponse(
        code="INTERNAL_ERROR",
        message=f"An unexpected error occurred. Reference: {error_id}",
    )
    return JSONResponse(status_code=500, content=body.model_dump())
```

### What to Log at Each Level

| Level | When | Example |
|-------|------|---------|
| `DEBUG` | Expected client errors (400, 404) | User requested missing resource |
| `WARNING` | Unexpected client errors, auth failures | 401, 403, 409, 422 |
| `ERROR` | Server errors (5xx) | Unhandled exception, downstream failure |
| `CRITICAL` | System-threatening failures | Database down, out of memory |

### What NOT to Log

- Passwords, tokens, API keys
- Full request bodies (may contain PII)
- Credit card numbers, SSNs
- Internal stack traces in the **response** (log them, don't return them)

---

## 9. Common HTTP Status Codes

### When to Use Each Code

| Code | Name | Use When | Example |
|------|------|----------|---------|
| **400** | Bad Request | Input is malformed or logically invalid (not a schema issue) | `"end_date must be after start_date"` |
| **401** | Unauthorized | No credentials or invalid credentials | Missing/expired JWT |
| **403** | Forbidden | Valid credentials, insufficient permissions | Regular user accessing admin endpoint |
| **404** | Not Found | Resource does not exist | `GET /users/999` when user 999 doesn't exist |
| **409** | Conflict | Operation conflicts with current state | Creating user with duplicate email |
| **422** | Unprocessable Entity | Schema validation failed (Pydantic) | Missing required field, wrong type |
| **429** | Too Many Requests | Rate limit exceeded | Client sent too many requests |
| **500** | Internal Server Error | Unhandled server-side failure | Unexpected exception |
| **502** | Bad Gateway | Upstream service returned an error | LLM vendor returned 500 |
| **503** | Service Unavailable | Server temporarily cannot handle requests | During deployment, overloaded |
| **504** | Gateway Timeout | Upstream service timed out | LLM vendor didn't respond in time |

### Decision Tree

```
Is the problem with the request?
├── Yes
│   ├── Credentials missing/invalid? → 401
│   ├── Credentials valid but not authorized? → 403
│   ├── Resource doesn't exist? → 404
│   ├── Conflicts with server state? → 409
│   ├── Rate limit exceeded? → 429
│   ├── Schema/type validation failed? → 422
│   └── Logically invalid? → 400
└── No (server-side problem)
    ├── Downstream service error? → 502
    ├── Downstream service timeout? → 504
    ├── Server temporarily overloaded? → 503
    └── Unknown/unexpected? → 500
```

### Common Mistakes with Status Codes

```python
# ❌ Using 400 for everything
raise HTTPException(400, "Not found")       # should be 404
raise HTTPException(400, "Not authorized")  # should be 403
raise HTTPException(400, "Duplicate email") # should be 409

# ❌ Using 500 for client errors
raise HTTPException(500, "Invalid input")   # should be 400 or 422

# ❌ Using 200 with error body
return {"status": "error", "message": "Not found"}  # should be 404

# ❌ Using 403 when you mean 401
raise HTTPException(403, "Token expired")   # should be 401
```

```python
# ✅ Correct status code usage
raise HTTPException(404, "User not found")
raise HTTPException(403, "Admin access required")
raise HTTPException(409, "Email already registered")
raise HTTPException(401, "Token expired")
```

---

## 10. Putting It All Together

### Complete Production Setup

```python
import uuid
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel

logger = structlog.get_logger()


# --- Error response schema ---

class ErrorDetail(BaseModel):
    field: str | None = None
    message: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []


# --- Domain exceptions ---

class AppException(Exception):
    def __init__(self, message: str, code: str | None = None):
        self.message = message
        self.code = code or self.__class__.__name__
        super().__init__(self.message)


class NotFoundError(AppException):
    pass

class ConflictError(AppException):
    pass

class AuthenticationError(AppException):
    pass

class AuthorizationError(AppException):
    pass

class RateLimitError(AppException):
    pass

class ExternalServiceError(AppException):
    def __init__(self, message: str, service: str):
        super().__init__(message, code="EXTERNAL_SERVICE_ERROR")
        self.service = service

class BusinessValidationError(AppException):
    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message, code="BUSINESS_VALIDATION_ERROR")
        self.details = [
            ErrorDetail(field=d.get("field"), message=d["message"])
            for d in (details or [])
        ]


# --- Exception-to-status mapping ---

EXCEPTION_STATUS_MAP: dict[type[AppException], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    AuthenticationError: 401,
    AuthorizationError: 403,
    BusinessValidationError: 422,
    RateLimitError: 429,
    ExternalServiceError: 502,
}


# --- Helper ---

def _build_response(status_code: int, code: str, message: str,
                    details: list[ErrorDetail] | None = None,
                    headers: dict | None = None) -> JSONResponse:
    body = ErrorResponse(code=code, message=message, details=details or [])
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers=headers,
    )


# --- App ---

app = FastAPI(
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)


# --- Exception handlers (specific → general) ---

@app.exception_handler(RateLimitError)
async def rate_limit_handler(request: Request, exc: RateLimitError):
    logger.warning("rate_limit_exceeded", path=request.url.path)
    return _build_response(429, exc.code, exc.message, headers={"Retry-After": "60"})


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    status_code = EXCEPTION_STATUS_MAP.get(type(exc), 500)
    log_method = logger.warning if status_code < 500 else logger.error
    log_method(
        "app_exception",
        exc_type=type(exc).__name__,
        exc_code=exc.code,
        status_code=status_code,
        path=request.url.path,
        method=request.method,
    )
    return _build_response(
        status_code, exc.code, exc.message,
        details=getattr(exc, "details", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    details = []
    for err in exc.errors():
        loc = err.get("loc", ())
        field = ".".join(str(p) for p in loc[1:]) if len(loc) > 1 else str(loc[0])
        details.append(ErrorDetail(field=field, message=err["msg"]))
    return _build_response(422, "VALIDATION_ERROR", "Request validation failed", details)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return _build_response(
        exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail),
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    error_id = str(uuid.uuid4())
    logger.error(
        "unhandled_exception",
        error_id=error_id,
        exc_type=type(exc).__name__,
        exc_message=str(exc),
        path=request.url.path,
        method=request.method,
        exc_info=True,
    )
    return _build_response(500, "INTERNAL_ERROR",
                           f"An unexpected error occurred. Reference: {error_id}")
```

### Using It in Endpoints

```python
from fastapi import Depends

@app.get("/users/{user_id}")
async def get_user(user_id: int, service: UserService = Depends(get_user_service)):
    # No try/except needed — exception handlers do the work
    return service.get_user(user_id)


@app.post("/users")
async def create_user(data: UserCreate, service: UserService = Depends(get_user_service)):
    return service.create_user(data.email)


@app.post("/orders")
async def create_order(data: OrderCreate, service: OrderService = Depends(get_order_service)):
    # Business validation raises BusinessValidationError
    # NotFoundError if product doesn't exist
    # ConflictError if duplicate order
    # All handled automatically
    return service.create_order(data)
```

The endpoints are clean. No error formatting logic. No status code decisions. Just business logic.

---

## 11. Common Mistakes

### Catching Exception Too Broadly

```python
# ❌ Swallows all domain exceptions — handlers never fire
@app.get("/users/{user_id}")
async def get_user(user_id: int):
    try:
        return service.get_user(user_id)
    except Exception:
        raise HTTPException(500, "Something went wrong")
```

```python
# ✅ Let exceptions propagate to handlers
@app.get("/users/{user_id}")
async def get_user(user_id: int):
    return service.get_user(user_id)
```

If you must catch specific exceptions in an endpoint, be explicit:

```python
# ✅ Catch only what you need to handle locally
@app.post("/transfer")
async def transfer(data: TransferRequest):
    try:
        return service.transfer(data.from_id, data.to_id, data.amount)
    except InsufficientFundsError as e:
        # Special case: need to add extra context
        raise HTTPException(
            status_code=400,
            detail={"code": "INSUFFICIENT_FUNDS", "balance": e.balance},
        )
    # Everything else propagates to global handlers
```

### Exposing Internal Details

```python
# ❌ Leaks database schema, library versions, stack traces
@app.exception_handler(Exception)
async def handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),  # "relation 'users' does not exist"
            "traceback": traceback.format_exc(),
        },
    )
```

```python
# ✅ Generic message to client, full details in logs
@app.exception_handler(Exception)
async def handler(request, exc):
    error_id = str(uuid.uuid4())
    logger.error("unhandled", error_id=error_id, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR",
                 "message": f"Internal error. Reference: {error_id}"},
    )
```

### Inconsistent Error Shapes

```python
# ❌ Three different error shapes from the same API
raise HTTPException(400, "bad request")                     # {"detail": "bad request"}
raise HTTPException(400, {"error": "bad"})                  # {"detail": {"error": "bad"}}
return JSONResponse(400, content={"message": "bad input"})  # {"message": "bad input"}
```

```python
# ✅ One shape everywhere — use the ErrorResponse model
raise AppException("bad request")  # Handled by global handler → standard shape
```

### Not Handling Starlette's HTTPException

FastAPI's `HTTPException` extends Starlette's, but middleware and other Starlette code may raise the base version. If you only override FastAPI's, some errors bypass your formatting.

```python
# ❌ Only handles FastAPI's HTTPException
from fastapi import HTTPException
@app.exception_handler(HTTPException)
async def handler(...): ...

# ✅ Handle both
from starlette.exceptions import HTTPException as StarletteHTTPException
@app.exception_handler(StarletteHTTPException)
async def handler(...): ...
```

### Using String Error Codes Instead of Enums

```python
# ❌ Typos in string codes go unnoticed
raise AppException("fail", code="USR_NOT_FOUND")   # typo
raise AppException("fail", code="USER_NOT_FOUND")  # correct
```

```python
# ✅ Use an enum or constants
class ErrorCode:
    USER_NOT_FOUND = "USER_NOT_FOUND"
    CONFLICT = "CONFLICT"
    VALIDATION = "VALIDATION_ERROR"
    INTERNAL = "INTERNAL_ERROR"
```

### Forgetting to Handle Background Task Errors

```python
# ❌ Exception in background task is silently swallowed
@app.post("/send-email")
async def send_email(bg: BackgroundTasks):
    bg.add_task(send_notification)  # If this throws, nobody knows
    return {"status": "queued"}
```

```python
# ✅ Wrap background tasks with error handling
async def safe_send_notification():
    try:
        await send_notification()
    except Exception:
        logger.error("background_task_failed", task="send_notification", exc_info=True)

@app.post("/send-email")
async def send_email(bg: BackgroundTasks):
    bg.add_task(safe_send_notification)
    return {"status": "queued"}
```

---

## 12. Summary

### The Architecture

```
Client Request
    │
    ▼
┌──────────────────────────┐
│  FastAPI Endpoint         │  ← no try/except (usually)
│  calls service layer      │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  Service / Domain Layer   │  ← raises NotFoundError, ConflictError, etc.
│  pure business logic      │
└──────────┬───────────────┘
           │ exception propagates
           ▼
┌──────────────────────────┐
│  Exception Handlers       │  ← maps exception → HTTP status + ErrorResponse
│  logging, formatting      │
└──────────┬───────────────┘
           │
           ▼
Client Response (consistent JSON shape)
```

### Key Principles

1. **Error responses are part of your API contract** — treat them as seriously as success responses
2. **Domain exceptions should not know about HTTP** — separation of concerns
3. **One error shape for all responses** — clients parse one format
4. **Global handlers over per-endpoint try/except** — centralize error formatting
5. **Log server errors, not client errors** — 4xx are informational, 5xx are actionable
6. **Never expose internals** — generic message to client, full details in logs
7. **Status codes have meaning** — use the right one for the situation

---

**Next**: Explore middleware patterns for request ID injection and error correlation across services.
