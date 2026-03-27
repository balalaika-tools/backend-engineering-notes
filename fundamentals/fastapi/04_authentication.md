# FastAPI Authentication & Security

A production guide to authentication, authorization, and security in FastAPI -- from mental model to battle-tested patterns.

---

## 1. The Mental Model

### Authentication vs Authorization

These are **two separate concerns**. Mixing them is the root of most security bugs.

| Concept | Question | When | Example |
|---------|----------|------|---------|
| **Authentication (AuthN)** | Who are you? | Before anything else | Verify JWT, check password |
| **Authorization (AuthZ)** | What can you do? | After identity is established | Check role, verify ownership |

Authentication without authorization: "I know who you are, but I let you do anything."
Authorization without authentication: "I don't know who you are, but I'll check your permissions." (Nonsensical.)

> **Rule**: Authenticate first. Authorize second. Never combine them into one step.

---

### Tokens vs Sessions

Two fundamentally different strategies for maintaining identity across requests.

| Aspect | Session-Based | Token-Based (JWT) |
|--------|---------------|-------------------|
| **State** | Server stores session data | Client stores token |
| **Storage** | Server-side (Redis, DB) | Client-side (header, cookie) |
| **Scalability** | Requires shared session store | Stateless -- any server can validate |
| **Revocation** | Delete session from store | Hard -- token valid until expiry |
| **Size** | Small session ID | Larger (payload + signature) |
| **Use case** | Traditional web apps | APIs, SPAs, microservices |

### Stateless vs Stateful Auth

**Stateless** (JWT): The token itself contains everything needed to verify identity. No database lookup required on each request.

**Stateful** (Sessions): The server must look up session data on every request. More control, more infrastructure.

> **For APIs, JWTs are the standard.** For traditional server-rendered apps, sessions still make sense. This guide focuses on token-based auth because that is what FastAPI APIs overwhelmingly use.

---

## 2. Password Hashing

### Why Never Store Plaintext

If your database is breached and passwords are stored in plaintext, every user account is immediately compromised. This is not hypothetical -- it happens constantly.

**Hashing is one-way.** You can verify a password against a hash, but you cannot reverse the hash to get the password.

### The Pattern: bcrypt via passlib

```python
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """Hash a password for storage."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a stored hash."""
    return pwd_context.verify(plain_password, hashed_password)
```

### How It Works

```python
hashed = hash_password("my_secret_password")
# "$2b$12$LJ3m4ys3Lg2yE1rkNqLiKe5GZWB5v1qh0VRlVaFHnPwK6zXmJbW6C"

verify_password("my_secret_password", hashed)   # True
verify_password("wrong_password", hashed)        # False
```

Key properties:
- **Salt is automatic** -- bcrypt generates a random salt per hash
- **Timing-safe comparison** -- passlib prevents timing attacks
- **Cost factor is configurable** -- `deprecated="auto"` handles algorithm upgrades

### Common Mistakes

**Do not do this:**

```python
# WRONG -- storing plaintext
user.password = request.password

# WRONG -- using MD5 or SHA256 (fast hashes, easily brute-forced)
import hashlib
user.password = hashlib.sha256(request.password.encode()).hexdigest()

# WRONG -- comparing hashes directly (timing attack vulnerable)
if stored_hash == pwd_context.hash(plain_password):
    ...
```

**Do this:**

```python
# CORRECT -- hash on registration
user.hashed_password = hash_password(request.password)

# CORRECT -- verify on login
if not verify_password(request.password, user.hashed_password):
    raise HTTPException(status_code=401, detail="Invalid credentials")
```

---

## 3. JWT Tokens

### Structure

A JWT is three Base64-encoded parts separated by dots:

```
header.payload.signature
```

| Part | Contains | Example |
|------|----------|---------|
| **Header** | Algorithm, token type | `{"alg": "HS256", "typ": "JWT"}` |
| **Payload** | Claims (user data, expiry) | `{"sub": "user@example.com", "exp": 1700000000}` |
| **Signature** | HMAC of header + payload | Prevents tampering |

> **The payload is NOT encrypted.** Anyone can decode it. The signature only guarantees it hasn't been tampered with.

### Creating Tokens with python-jose

```python
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError

SECRET_KEY = "your-secret-key-from-environment"  # NEVER hardcode in production
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises JWTError on failure."""
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
```

### Token Expiration

**Every token must expire.** A token without expiration is a permanent credential.

```python
# Short-lived access token (15-30 minutes)
access_token = create_access_token(
    data={"sub": user.email},
    expires_delta=timedelta(minutes=30),
)

# python-jose automatically rejects expired tokens
try:
    payload = decode_access_token(token)
except JWTError:
    raise HTTPException(status_code=401, detail="Token invalid or expired")
```

### Refresh Tokens

Access tokens are short-lived. Refresh tokens let users get new access tokens without re-authenticating.

```python
REFRESH_TOKEN_EXPIRE_DAYS = 7


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


@app.post("/auth/refresh")
def refresh_access_token(refresh_token: str):
    try:
        payload = decode_access_token(refresh_token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")

    # Issue new access token
    new_access_token = create_access_token(data={"sub": payload["sub"]})
    return {"access_token": new_access_token, "token_type": "bearer"}
```

| Token Type | Lifetime | Storage | Purpose |
|------------|----------|---------|---------|
| **Access token** | 15-30 minutes | Memory / JS variable | Authorize API requests |
| **Refresh token** | 7-30 days | HttpOnly cookie / secure storage | Get new access tokens |

---

## 4. OAuth2PasswordBearer -- FastAPI's Built-In Flow

### What It Does

`OAuth2PasswordBearer` is a FastAPI class that:
1. Declares a token URL for the OpenAPI docs (Swagger UI "Authorize" button)
2. Extracts the `Bearer` token from the `Authorization` header
3. Returns the raw token string

It does **NOT** validate the token. That is your job.

### The Login Flow

```python
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

app = FastAPI()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


class Token(BaseModel):
    access_token: str
    token_type: str


# Simulated user lookup -- replace with real DB query
def authenticate_user(email: str, password: str):
    user = get_user_by_email(email)  # your DB lookup
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


@app.post("/auth/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}
```

### How the Flow Works

```
1. Client sends POST /auth/login with username + password (form data)
2. Server validates credentials
3. Server returns {"access_token": "eyJ...", "token_type": "bearer"}
4. Client sends subsequent requests with header: Authorization: Bearer eyJ...
5. oauth2_scheme extracts "eyJ..." from the header
```

> **Note**: `OAuth2PasswordRequestForm` expects `username` and `password` as **form fields**, not JSON. This is the OAuth2 spec. Your "username" field can contain an email -- the field name is just `username`.

---

## 5. Dependency-Based Auth

This is where FastAPI's dependency injection system makes auth elegant. You build a chain of dependencies that authenticate and authorize in clean, composable layers.

### The `get_current_user` Dependency

```python
from pydantic import BaseModel


class User(BaseModel):
    email: str
    is_active: bool
    role: str  # "user", "admin", "moderator"


def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = get_user_by_email(email)  # your DB lookup
    if user is None:
        raise credentials_exception
    return user


def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user
```

### Using It in Endpoints

```python
@app.get("/users/me")
def read_current_user(user: User = Depends(get_current_active_user)):
    return user


@app.get("/users/me/orders")
def read_my_orders(user: User = Depends(get_current_active_user)):
    return get_orders_for_user(user.email)
```

### Role-Based Authorization

```python
from functools import wraps
from typing import Callable


def require_role(required_role: str) -> Callable:
    """Dependency factory that checks user role."""
    def role_checker(user: User = Depends(get_current_active_user)) -> User:
        if user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{required_role}' required",
            )
        return user
    return role_checker


@app.get("/admin/dashboard")
def admin_dashboard(user: User = Depends(require_role("admin"))):
    return {"message": f"Welcome admin {user.email}"}


@app.delete("/admin/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_role("admin"))):
    # Only admins can delete users
    return {"deleted": user_id}
```

### Permission Guards (Fine-Grained)

For more granular control than roles:

```python
def require_permissions(*permissions: str) -> Callable:
    """Check that user has ALL required permissions."""
    def permission_checker(user: User = Depends(get_current_active_user)) -> User:
        user_permissions = get_user_permissions(user)  # your lookup
        missing = set(permissions) - set(user_permissions)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {missing}",
            )
        return user
    return permission_checker


@app.delete("/posts/{post_id}")
def delete_post(
    post_id: int,
    user: User = Depends(require_permissions("posts:delete")),
):
    return {"deleted": post_id}
```

### The Dependency Chain

```
oauth2_scheme          → extracts token from header
    ↓
get_current_user       → decodes token, fetches user (AuthN)
    ↓
get_current_active_user → checks user is active
    ↓
require_role("admin")   → checks user role (AuthZ)
```

Each layer has **one responsibility**. Each can be tested independently. Each can be overridden in tests.

---

## 6. CORS (Cross-Origin Resource Sharing)

### What It Is

CORS is a browser security mechanism. When your frontend (e.g., `https://app.example.com`) calls your API (e.g., `https://api.example.com`), the browser blocks the request unless the API explicitly allows it.

**CORS only affects browsers.** Curl, Postman, and server-to-server calls are not affected.

### Why It Matters

Without CORS configuration:
- Your frontend cannot call your API from a different origin
- You will get cryptic browser errors with zero server-side logs
- The browser sends a **preflight OPTIONS request** before the actual request -- your API must respond to it

### FastAPI CORSMiddleware

```python
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com", "https://staging.example.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

### Configuration Reference

| Parameter | Purpose | Recommendation |
|-----------|---------|----------------|
| `allow_origins` | Which origins can call the API | List specific domains |
| `allow_credentials` | Allow cookies / auth headers | `True` if using auth |
| `allow_methods` | Which HTTP methods | List only what you use |
| `allow_headers` | Which request headers | List only what you need |
| `expose_headers` | Which response headers the browser can read | As needed |
| `max_age` | How long to cache preflight responses (seconds) | `600` (10 minutes) |

### Common CORS Mistakes

**Do not do this:**

```python
# WRONG -- allows any website to call your API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,  # This combination is actually rejected by browsers
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Problems with `allow_origins=["*"]`:**
- Any website can make requests to your API on behalf of your users
- Combined with `allow_credentials=True`, browsers will actually **reject** this (spec violation)
- In production, this is a security hole

**Do this:**

```python
# CORRECT -- explicit origins
import os

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # ["https://app.example.com"]
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

### Development vs Production

```python
import os

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

if ENVIRONMENT == "development":
    origins = ["http://localhost:3000", "http://localhost:5173"]
else:
    origins = ["https://app.example.com"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

---

## 7. API Key Authentication

### When to Use API Keys

| Auth Method | Use Case |
|-------------|----------|
| JWT + OAuth2 | User-facing APIs, SPAs, mobile apps |
| API Keys | Service-to-service, third-party integrations, machine clients |

API keys are simpler but less flexible. They identify a **client**, not a **user**.

### Header-Based API Key Dependency

```python
from fastapi import Security
from fastapi.security import APIKeyHeader

API_KEY_HEADER = APIKeyHeader(name="X-API-Key")


def get_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    valid_keys = get_valid_api_keys()  # from DB or config
    if api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return api_key


@app.get("/external/data")
def get_data(api_key: str = Depends(get_api_key)):
    return {"data": "sensitive information"}
```

### API Key with Client Identification

```python
class APIClient(BaseModel):
    name: str
    permissions: list[str]
    rate_limit: int


def get_api_client(api_key: str = Security(API_KEY_HEADER)) -> APIClient:
    """Resolve API key to a client with permissions and rate limits."""
    client = lookup_client_by_key(api_key)  # your DB lookup
    if not client:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return client


@app.get("/partner/orders")
def get_partner_orders(client: APIClient = Depends(get_api_client)):
    if "orders:read" not in client.permissions:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return get_orders_for_client(client.name)
```

### Supporting Both JWT and API Key

```python
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_current_user_or_client(
    token: str | None = Depends(oauth2_scheme),
    api_key: str | None = Security(api_key_header),
) -> User | APIClient:
    """Accept either JWT or API key authentication."""
    if token:
        return decode_and_get_user(token)
    if api_key:
        return lookup_client_by_key(api_key)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No authentication provided",
    )
```

---

## 8. Security Headers

### HTTPS Enforcement

**Never run production without HTTPS.** JWTs sent over HTTP can be intercepted trivially.

FastAPI itself does not handle TLS. Your reverse proxy (Nginx, Caddy, a load balancer) terminates TLS. But you can enforce HTTPS redirect:

```python
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware

# Only in production -- breaks local development
if ENVIRONMENT == "production":
    app.add_middleware(HTTPSRedirectMiddleware)
```

### Trusted Host Middleware

Prevent host header attacks:

```python
from fastapi.middleware.trustedhost import TrustedHostMiddleware

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["api.example.com", "*.example.com"],
)
```

### Common Security Headers

Add security headers via middleware:

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)
```

### Security Headers Reference

| Header | Purpose | Value |
|--------|---------|-------|
| `X-Content-Type-Options` | Prevent MIME-type sniffing | `nosniff` |
| `X-Frame-Options` | Prevent clickjacking | `DENY` |
| `Strict-Transport-Security` | Force HTTPS | `max-age=31536000; includeSubDomains` |
| `Referrer-Policy` | Control referrer information | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | Restrict browser features | Disable unused APIs |

---

## 9. Common Patterns

### Protecting Routes

```python
# Protected by default -- requires valid token
@app.get("/users/me")
def get_me(user: User = Depends(get_current_active_user)):
    return user


# Protected at router level -- all routes in this router require auth
admin_router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_role("admin"))],
)

@admin_router.get("/stats")
def admin_stats():
    # User is already verified as admin by router dependency
    return get_system_stats()

@admin_router.delete("/users/{user_id}")
def admin_delete_user(user_id: int):
    return delete_user(user_id)

app.include_router(admin_router)
```

### Optional Authentication

Some endpoints work for both authenticated and anonymous users:

```python
def get_optional_user(
    token: str | None = Depends(OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)),
) -> User | None:
    """Returns user if authenticated, None otherwise."""
    if token is None:
        return None
    try:
        payload = decode_access_token(token)
        email = payload.get("sub")
        if email is None:
            return None
        return get_user_by_email(email)
    except JWTError:
        return None


@app.get("/posts/{post_id}")
def get_post(post_id: int, user: User | None = Depends(get_optional_user)):
    post = get_post_by_id(post_id)
    if user:
        post.is_liked = check_user_liked(user.email, post_id)
    return post
```

### Resource Ownership Check

```python
def get_own_resource(resource_id: int, user: User = Depends(get_current_active_user)):
    """Verify user owns the resource they're trying to access."""
    resource = get_resource_by_id(resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.owner_email != user.email and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your resource")
    return resource


@app.put("/posts/{post_id}")
def update_post(post_id: int, body: PostUpdate, resource=Depends(get_own_resource)):
    return update_post_in_db(resource, body)
```

### Rate Limiting Per User

```python
from collections import defaultdict
from datetime import datetime, timezone

# Simple in-memory rate limiter (use Redis in production)
request_counts: dict[str, list[datetime]] = defaultdict(list)


def rate_limit(
    max_requests: int = 100,
    window_seconds: int = 60,
):
    def limiter(user: User = Depends(get_current_active_user)) -> User:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=window_seconds)

        # Clean old entries
        request_counts[user.email] = [
            t for t in request_counts[user.email] if t > window_start
        ]

        if len(request_counts[user.email]) >= max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )

        request_counts[user.email].append(now)
        return user

    return limiter


@app.post("/api/expensive-operation")
def expensive_op(user: User = Depends(rate_limit(max_requests=10, window_seconds=60))):
    return perform_expensive_operation(user)
```

---

## 10. Common Mistakes

### Storing Secrets in Code

```python
# WRONG -- secret in source code
SECRET_KEY = "super-secret-key-12345"
DATABASE_URL = "postgresql://user:password@host/db"
```

```python
# CORRECT -- from environment
import os

SECRET_KEY = os.environ["SECRET_KEY"]  # Fails loudly if missing
DATABASE_URL = os.environ["DATABASE_URL"]
```

Or with Pydantic Settings:

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    secret_key: str
    database_url: str
    allowed_origins: list[str] = ["http://localhost:3000"]

    model_config = {"env_file": ".env"}


settings = Settings()
```

---

### Not Validating Token Expiry

```python
# WRONG -- decoding without expiry validation
payload = jwt.decode(token, SECRET_KEY, options={"verify_exp": False})
```

```python
# CORRECT -- let the library enforce expiry (default behavior)
payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
# Raises ExpiredSignatureError if token is expired
```

---

### Overly Permissive CORS

```python
# WRONG -- any origin, all methods, all headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

```python
# CORRECT -- explicit whitelist
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

---

### Leaking Information in Error Messages

```python
# WRONG -- tells attacker which field is wrong
if not user:
    raise HTTPException(status_code=401, detail="User not found")
if not verify_password(password, user.hashed_password):
    raise HTTPException(status_code=401, detail="Wrong password")
```

```python
# CORRECT -- same error for both cases
if not user or not verify_password(password, user.hashed_password):
    raise HTTPException(status_code=401, detail="Incorrect email or password")
```

---

### Using `auto_error=True` When You Need Optional Auth

```python
# WRONG -- returns 401 for unauthenticated users on public endpoints
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")  # auto_error=True by default

@app.get("/public-feed")
def public_feed(token: str = Depends(oauth2_scheme)):
    # Anonymous users get 401 -- but this should be public!
    ...
```

```python
# CORRECT -- auto_error=False for optional auth
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

@app.get("/public-feed")
def public_feed(token: str | None = Depends(oauth2_scheme_optional)):
    user = decode_user_from_token(token) if token else None
    return get_feed(user=user)
```

---

### Not Setting Token Type in Refresh Tokens

```python
# WRONG -- access and refresh tokens are interchangeable
def create_access_token(data: dict) -> str:
    return jwt.encode({**data, "exp": ...}, SECRET_KEY)

def create_refresh_token(data: dict) -> str:
    return jwt.encode({**data, "exp": ...}, SECRET_KEY)
# Attacker can use a refresh token as an access token!
```

```python
# CORRECT -- tokens have explicit types
def create_access_token(data: dict) -> str:
    return jwt.encode({**data, "exp": ..., "type": "access"}, SECRET_KEY)

def create_refresh_token(data: dict) -> str:
    return jwt.encode({**data, "exp": ..., "type": "refresh"}, SECRET_KEY)

def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_access_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    ...
```

---

### Missing Rate Limiting on Auth Endpoints

```python
# WRONG -- no rate limiting on login
@app.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # Attacker can brute-force passwords at full speed
    ...
```

Auth endpoints (login, register, password reset) should **always** be rate-limited. Use a reverse proxy (Nginx, Cloudflare) or application-level rate limiting.

---

## 11. Production Checklist

| Concern | Action |
|---------|--------|
| **Secrets** | Load from environment variables, never from code |
| **Passwords** | Hash with bcrypt via passlib, never store plaintext |
| **JWTs** | Set expiration, validate on every request, use token types |
| **CORS** | Whitelist specific origins, never `*` in production |
| **HTTPS** | Enforce via reverse proxy, add HSTS header |
| **Error messages** | Never leak whether a user exists or which field is wrong |
| **Rate limiting** | Protect login and registration endpoints |
| **Dependencies** | Build auth as composable dependency chains |
| **Headers** | Add security headers via middleware |
| **Refresh tokens** | Store securely, distinguish from access tokens |

---

## 12. Complete Example: Putting It All Together

```python
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel

# --- Configuration ---

SECRET_KEY = os.environ["SECRET_KEY"]
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# --- Security utilities ---

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


# --- Models ---

class User(BaseModel):
    email: str
    is_active: bool
    role: str


class Token(BaseModel):
    access_token: str
    token_type: str


# --- Auth functions ---

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# --- Dependencies ---

def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = get_user_by_email(email)  # your DB lookup
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


def require_role(role: str):
    def checker(user: User = Depends(get_current_active_user)) -> User:
        if user.role != role:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker


# --- App ---

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# --- Routes ---

@app.post("/auth/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = get_user_by_email(form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_access_token(data={"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}


@app.get("/users/me")
def read_me(user: User = Depends(get_current_active_user)):
    return user


@app.get("/admin/stats")
def admin_stats(admin: User = Depends(require_role("admin"))):
    return {"users": 1000, "active": 850}
```

---

## Key Principles

1. **Auth is a dependency chain** -- each layer has one job
2. **Tokens are not encrypted** -- they are signed; never put secrets in the payload
3. **Passwords must be hashed** -- bcrypt via passlib, never anything else
4. **CORS is not optional** -- misconfigured CORS either blocks your frontend or opens security holes
5. **Secrets come from the environment** -- never from source code
6. **Errors should not leak information** -- same message for wrong user and wrong password
7. **Every token must expire** -- a token without expiry is a permanent key
