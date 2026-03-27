# Environment & Configuration in Python — A Production Guide

This guide explains **how to manage configuration in Python backend applications** — from local development to production. Covers the 12-factor methodology, pydantic-settings, secrets management, and real-world patterns for FastAPI.

---

## 1. The Core Principle: Config Belongs in the Environment

The [12-factor app](https://12factor.net/config) methodology defines one rule for configuration:

> **Everything that changes between deploys (dev, staging, prod) must come from the environment — never from code.**

This includes:

- Database URLs
- API keys and secrets
- Feature flags
- Debug mode
- External service endpoints
- Port numbers, worker counts

### Why This Matters

```python
# ❌ Config in code — must redeploy to change anything
DATABASE_URL = "postgresql://user:pass@prod-db:5432/myapp"
STRIPE_KEY = "sk_live_abc123"
DEBUG = False
```

```python
# ✅ Config from environment — same code runs everywhere
import os
DATABASE_URL = os.environ["DATABASE_URL"]
STRIPE_KEY = os.environ["STRIPE_KEY"]
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
```

The second version can run in dev, staging, and prod **without any code changes**. The environment provides the values.

### The Problem with `os.environ`

Raw `os.environ` works but has real issues:

- No type conversion — everything is a string
- No validation — missing vars crash at runtime, not startup
- No defaults — manual `.get()` everywhere
- No documentation — you discover required vars by reading code
- No nesting — flat namespace only

This is where pydantic-settings comes in.

---

## 2. pydantic-settings — Typed Configuration

`pydantic-settings` gives you validated, typed, documented configuration using Pydantic models.

```bash
pip install pydantic-settings
```

### Basic Settings Class

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://localhost:6379"
    debug: bool = False
    api_key: str
    max_connections: int = 10
```

### How It Works

`BaseSettings` reads values from **environment variables** automatically:

```bash
export DATABASE_URL="postgresql://user:pass@localhost:5432/myapp"
export API_KEY="sk_test_abc123"
```

```python
settings = Settings()
print(settings.database_url)  # postgresql://user:pass@localhost:5432/myapp
print(settings.debug)         # False (default)
print(settings.max_connections)  # 10 (default)
```

### Resolution Order

pydantic-settings resolves values in this order (first match wins):

| Priority | Source              | Example                              |
| -------- | ------------------- | ------------------------------------ |
| 1        | Init kwargs         | `Settings(debug=True)`               |
| 2        | Environment vars    | `export DEBUG=true`                  |
| 3        | `.env` file         | `DEBUG=true` in `.env`               |
| 4        | Field default       | `debug: bool = False`                |

If no source provides a value and there is no default, startup fails with a validation error. This is exactly what you want — fail fast.

---

## 3. `.env` Files

### Loading `.env` Files

Configure your settings class to read from `.env`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    database_url: str
    redis_url: str = "redis://localhost:6379"
    debug: bool = False
    api_key: str
```

### `.env` File Format

```bash
# .env — local development only
DATABASE_URL=postgresql://user:pass@localhost:5432/myapp
REDIS_URL=redis://localhost:6379
API_KEY=sk_test_local_key
DEBUG=true
```

### The `.env.example` Pattern

Your `.env` file contains real secrets — it must never be committed. Instead, commit a `.env.example` that documents every variable:

```bash
# .env.example — committed to git, documents all config vars
DATABASE_URL=postgresql://user:pass@localhost:5432/myapp
REDIS_URL=redis://localhost:6379
API_KEY=your-api-key-here
DEBUG=true
```

New developers clone the repo, copy `.env.example` to `.env`, and fill in real values:

```bash
cp .env.example .env
# edit .env with real credentials
```

### .gitignore Rules

```gitignore
# ❌ NEVER commit these
.env
.env.local
.env.production

# ✅ ALWAYS commit these
.env.example
```

---

## 4. Settings Validation

### Type Coercion

pydantic-settings automatically converts environment variable strings to Python types:

```python
class Settings(BaseSettings):
    debug: bool = False          # "true" → True, "1" → True, "false" → False
    port: int = 8000             # "8000" → 8000
    rate_limit: float = 1.5      # "1.5" → 1.5
    allowed_hosts: list[str] = ["localhost"]  # '["localhost","example.com"]' → list
    workers: int | None = None   # unset → None
```

### Required vs Optional

```python
class Settings(BaseSettings):
    # Required — no default, startup fails if missing
    database_url: str
    secret_key: str

    # Optional with defaults
    debug: bool = False
    port: int = 8000

    # Optional, can be None
    sentry_dsn: str | None = None
    redis_url: str | None = None
```

### Custom Validators

```python
from pydantic import field_validator

class Settings(BaseSettings):
    database_url: str
    port: int = 8000
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1024 <= v <= 65535):
            raise ValueError("Port must be between 1024 and 65535")
        return v
```

### Validation at Startup

If any required variable is missing or any validator fails, you get a clear error **immediately** — not at 3 AM when a request first hits that code path:

```
pydantic_core._pydantic_core.ValidationError: 2 validation errors for Settings
database_url
  Field required [type=missing, input_value={}, input_type=dict]
secret_key
  Field required [type=missing, input_value={}, input_type=dict]
```

---

## 5. Nested Settings

For large applications, group related config into nested models:

```python
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

class DatabaseSettings(BaseModel):
    url: str
    pool_size: int = 5
    pool_timeout: int = 30
    echo: bool = False

class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379"
    ttl: int = 3600

class AuthSettings(BaseModel):
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",   # enables SECTION__KEY format
    )

    environment: str = "development"
    debug: bool = False
    database: DatabaseSettings
    redis: RedisSettings = RedisSettings()
    auth: AuthSettings
```

With `env_nested_delimiter="__"`, set nested values like:

```bash
DATABASE__URL=postgresql://user:pass@localhost:5432/myapp
DATABASE__POOL_SIZE=20
DATABASE__ECHO=true
REDIS__URL=redis://prod-redis:6379
REDIS__TTL=7200
AUTH__SECRET_KEY=super-secret-key
AUTH__ACCESS_TOKEN_EXPIRE_MINUTES=60
ENVIRONMENT=production
```

Access them cleanly:

```python
settings = Settings()
settings.database.url          # "postgresql://..."
settings.database.pool_size    # 20
settings.auth.secret_key       # "super-secret-key"
```

---

## 6. The Singleton Pattern — `@lru_cache`

Settings should be loaded **once** and reused. Creating a new `Settings()` instance on every request re-reads environment variables and `.env` files needlessly.

```python
from functools import lru_cache

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### Why `@lru_cache`

| Approach            | Reads env | Parses `.env` | Validates | Per call |
| ------------------- | --------- | ------------- | --------- | -------- |
| `Settings()` direct | Yes       | Yes           | Yes       | Every time |
| `@lru_cache`        | Once      | Once          | Once      | Cached   |

### Usage in FastAPI with Dependency Injection

```python
from functools import lru_cache
from fastapi import Depends, FastAPI

app = FastAPI()

@lru_cache
def get_settings() -> Settings:
    return Settings()

@app.get("/info")
def info(settings: Settings = Depends(get_settings)):
    return {
        "environment": settings.environment,
        "debug": settings.debug,
    }
```

This gives you:

- **Singleton** — settings loaded once, reused across all requests
- **Testable** — override via `app.dependency_overrides[get_settings]`
- **Explicit** — every endpoint declares its dependency on config

### Testing with Overrides

```python
from fastapi.testclient import TestClient

def get_test_settings():
    return Settings(
        database_url="sqlite:///test.db",
        secret_key="test-secret",
        debug=True,
    )

app.dependency_overrides[get_settings] = get_test_settings
client = TestClient(app)
```

---

## 7. Environment-Specific Configuration

### The `ENVIRONMENT` Variable

Use a single variable to identify the deployment environment:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    environment: str = "development"    # development | staging | production
    debug: bool = False
    database_url: str
    log_level: str = "INFO"
```

```bash
# Development
ENVIRONMENT=development
DEBUG=true
DATABASE_URL=postgresql://user:pass@localhost:5432/myapp_dev
LOG_LEVEL=DEBUG

# Production
ENVIRONMENT=production
DEBUG=false
DATABASE_URL=postgresql://user:pass@prod-db:5432/myapp
LOG_LEVEL=WARNING
```

### Computed Properties Based on Environment

```python
from pydantic import computed_field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    environment: str = "development"
    debug: bool = False

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @computed_field
    @property
    def is_development(self) -> bool:
        return self.environment == "development"
```

Usage:

```python
settings = get_settings()

if settings.is_production:
    sentry_sdk.init(dsn=settings.sentry_dsn)

if settings.is_development:
    app = FastAPI(docs_url="/docs")
else:
    app = FastAPI(docs_url=None)  # disable docs in prod
```

### Per-Environment `.env` Files

```python
class Settings(BaseSettings):
    environment: str = "development"

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),  # .env.local overrides .env
    )
```

---

## 8. Secrets Management

### The Rule

> **Secrets must never exist in your codebase, your git history, or your Docker images.**

### Development: `.env` File

In development, `.env` is fine for secrets — just keep it out of git:

```bash
# .env (local dev only, in .gitignore)
DATABASE_URL=postgresql://dev:dev@localhost:5432/myapp
SECRET_KEY=not-a-real-secret
STRIPE_KEY=sk_test_abc123
```

### Production: Environment Variables or Mounted Secrets

In production, secrets come from the platform — not files in your repo:

| Platform         | How secrets are provided             |
| ---------------- | ------------------------------------ |
| Docker / K8s     | Environment vars or mounted files    |
| AWS ECS          | Secrets Manager → env vars           |
| Railway / Render | Dashboard → env vars                 |
| Vault            | API call at startup or sidecar       |

#### Kubernetes Secrets as Environment Variables

```yaml
# k8s deployment
env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: myapp-secrets
        key: database-url
  - name: SECRET_KEY
    valueFrom:
      secretKeyRef:
        name: myapp-secrets
        key: secret-key
```

#### Kubernetes Secrets as Mounted Files

```yaml
volumes:
  - name: secrets
    secret:
      secretName: myapp-secrets
volumeMounts:
  - name: secrets
    mountPath: /run/secrets
    readOnly: true
```

pydantic-settings can read from secrets files:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        secrets_dir="/run/secrets",
    )

    database_url: str
    secret_key: str
```

With `secrets_dir`, pydantic-settings reads `/run/secrets/database_url` as the value for `database_url`.

### What Not to Do

```python
# ❌ Hardcoded secret
SECRET_KEY = "my-super-secret-key-12345"

# ❌ Secret in default value
class Settings(BaseSettings):
    api_key: str = "sk_live_real_production_key"

# ❌ Secret in committed .env
# (if .env is not in .gitignore)

# ❌ Secret in Docker image
COPY .env /app/.env

# ❌ Secret in git history
# (even if you deleted the file, it's still in history)
```

---

## 9. Complete Production Example

### Project Structure

```
myapp/
  app/
    __init__.py
    main.py
    config.py          # all settings live here
    dependencies.py    # FastAPI dependency functions
    routes/
      ...
  .env                 # local dev only (gitignored)
  .env.example         # documents all variables (committed)
  .gitignore
```

### `config.py`

```python
from pydantic import BaseModel, field_validator, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    url: str
    pool_size: int = 5
    pool_timeout: int = 30


class AuthSettings(BaseModel):
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    # Application
    environment: str = "development"
    debug: bool = False
    app_name: str = "myapp"
    log_level: str = "INFO"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1

    # Database
    database: DatabaseSettings

    # Auth
    auth: AuthSettings

    # External services
    sentry_dsn: str | None = None
    stripe_key: str | None = None

    # Feature flags
    enable_signups: bool = True
    enable_webhooks: bool = False

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Invalid log level: {v}")
        return v

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.environment == "production"
```

### `dependencies.py`

```python
from functools import lru_cache
from .config import Settings

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### `main.py`

```python
from fastapi import Depends, FastAPI
from .config import Settings
from .dependencies import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    docs_url="/docs" if not settings.is_production else None,
)

@app.get("/health")
def health(settings: Settings = Depends(get_settings)):
    return {
        "status": "ok",
        "environment": settings.environment,
    }
```

### `.env.example`

```bash
# Application
ENVIRONMENT=development
DEBUG=true
APP_NAME=myapp
LOG_LEVEL=DEBUG

# Server
HOST=0.0.0.0
PORT=8000
WORKERS=1

# Database
DATABASE__URL=postgresql://user:pass@localhost:5432/myapp
DATABASE__POOL_SIZE=5

# Auth
AUTH__SECRET_KEY=change-me-in-production
AUTH__ACCESS_TOKEN_EXPIRE_MINUTES=30

# External services (optional)
SENTRY_DSN=
STRIPE_KEY=

# Feature flags
ENABLE_SIGNUPS=true
ENABLE_WEBHOOKS=false
```

### `.gitignore`

```gitignore
.env
.env.local
.env.production
.env.*.local
```

---

## 10. Common Patterns

### Database URL

```python
class Settings(BaseSettings):
    database_url: str

    # Or build from components:
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = ""
    db_name: str = "myapp"

    @computed_field
    @property
    def database_url_computed(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )
```

### Feature Flags

```python
class Settings(BaseSettings):
    enable_signups: bool = True
    enable_dark_mode: bool = False
    enable_beta_features: bool = False
    max_upload_size_mb: int = 10
```

```python
@app.post("/signup")
def signup(settings: Settings = Depends(get_settings)):
    if not settings.enable_signups:
        raise HTTPException(status_code=403, detail="Signups are disabled")
    ...
```

### CORS Origins

```python
class Settings(BaseSettings):
    cors_origins: list[str] = ["http://localhost:3000"]
```

```bash
CORS_ORIGINS='["http://localhost:3000","https://myapp.com"]'
```

### Debug Mode Guard

```python
@app.get("/debug/config")
def debug_config(settings: Settings = Depends(get_settings)):
    if not settings.debug:
        raise HTTPException(status_code=404)
    # Never expose secrets — even in debug mode
    return {
        "environment": settings.environment,
        "debug": settings.debug,
        "log_level": settings.log_level,
        "features": {
            "signups": settings.enable_signups,
            "webhooks": settings.enable_webhooks,
        },
    }
```

---

## 11. Common Mistakes

### Hardcoded secrets

```python
# ❌ Secret in source code
JWT_SECRET = "my-jwt-secret-key"

# ✅ Secret from environment
class Settings(BaseSettings):
    jwt_secret: str   # no default — required from env
```

### Missing `.env` in `.gitignore`

```python
# ❌ .env committed to git — secrets in history forever
# Even deleting the file later doesn't remove it from history

# ✅ Add .env to .gitignore BEFORE your first commit
```

### Mutable global settings

```python
# ❌ Mutable — any code can corrupt config at runtime
settings = {"debug": True, "db_url": "..."}
settings["debug"] = False  # someone does this somewhere

# ✅ Frozen Pydantic model — immutable after creation
class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True)
    debug: bool = False
```

### Creating settings per request

```python
# ❌ Re-reads env + .env on every single request
@app.get("/items")
def items():
    settings = Settings()   # expensive and unnecessary
    ...

# ✅ Cached singleton via @lru_cache
@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### Using `os.environ` directly alongside pydantic-settings

```python
# ❌ Mixed approaches — config scattered across two systems
class Settings(BaseSettings):
    database_url: str

settings = get_settings()
debug = os.environ.get("DEBUG", "false")  # why not in Settings?

# ✅ All config in one place
class Settings(BaseSettings):
    database_url: str
    debug: bool = False
```

### Secrets in default values

```python
# ❌ Default value is a real secret — ends up in git
class Settings(BaseSettings):
    api_key: str = "sk_live_abc123_real_key"

# ✅ No default for secrets — force them from environment
class Settings(BaseSettings):
    api_key: str   # required, no default
```

---

## 12. Quick Reference

### Startup Checklist

1. All secrets come from environment (never code)
2. `.env` is in `.gitignore`
3. `.env.example` documents every variable
4. Settings class validates types and constraints
5. `@lru_cache` prevents repeated loading
6. FastAPI accesses settings via `Depends(get_settings)`
7. No `os.environ` calls scattered through business logic

### Settings Class Template

```python
from functools import lru_cache
from pydantic import field_validator, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        frozen=True,
    )

    environment: str = "development"
    debug: bool = False

    # Add your config fields here

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### Key Decisions Table

| Decision                      | Development           | Production                   |
| ----------------------------- | --------------------- | ---------------------------- |
| Secrets source                | `.env` file           | Platform env vars / secrets  |
| Debug mode                    | `True`                | `False`                      |
| `.env` file                   | Exists, gitignored    | Not used (env vars instead)  |
| Log level                     | `DEBUG`               | `WARNING` or `ERROR`         |
| Docs endpoint                 | `/docs`               | Disabled (`None`)            |
| Settings validation           | Same as prod          | Same as dev                  |
| `@lru_cache` settings         | Yes                   | Yes                          |

---
