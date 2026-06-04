# Docker and Deployment for FastAPI

A production-focused guide to containerizing and deploying FastAPI applications — from Dockerfile to graceful shutdown.

---

## 1. The Mental Model

Deploying a FastAPI app is not "put code in a container." It is a series of decisions about **isolation, reproducibility, and failure handling**.

The deployment stack:

```
Your Code
  ↓
ASGI Server (Uvicorn)
  ↓
Process Manager (Gunicorn, optional)
  ↓
Container (Docker)
  ↓
Orchestrator (Compose / Kubernetes)
  ↓
Infrastructure
```

Each layer has a specific job. Skipping or misconfiguring any layer creates production problems that are hard to debug.

---

## 2. Dockerfile for FastAPI

### The Production Dockerfile

```dockerfile
# ---- Stage 1: Build ----
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies (gcc, etc.) only in build stage
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Stage 2: Runtime ----
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy only installed packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -s /sbin/nologin appuser

# Copy application code last (best cache utilization)
COPY ./app ./app

# Switch to non-root user
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Why Multi-Stage Builds

| Concern | Single stage | Multi-stage |
|---------|-------------|-------------|
| Image size | 800MB+ (gcc, dev headers) | ~150MB (runtime only) |
| Attack surface | Build tools in production | Minimal runtime |
| Build cache | Poor (one layer) | Good (builder cached separately) |
| Secrets exposure | Build-time secrets in final image | Build-time artifacts discarded |

### Why `python:3.12-slim` Over `python:3.12`

```
python:3.12       → ~1.0 GB (full Debian, docs, man pages)
python:3.12-slim  → ~150 MB (minimal Debian)
python:3.12-alpine → ~50 MB (musl libc — smaller but trickier)
```

> **Default to `slim`.** Alpine uses `musl` instead of `glibc`. The long-standing pain — no binary wheels, long compile-from-source installs — has mostly been resolved: PEP 656 added the `musllinux` wheel tag, and as of 2024–2025 numpy, pandas, psycopg2-binary, scipy, matplotlib, and most of the scientific stack ship `musllinux_1_2` wheels on PyPI. The cases where Alpine still hurts are: (a) packages that have not yet published musllinux wheels — check with `pip install --only-binary=:all: <pkg>`; (b) subtle runtime differences (DNS resolver behavior, thread-local stacks) that can surface as hard-to-diagnose bugs. If you want the smaller image and your dependency tree installs cleanly from wheels, Alpine is fine. Otherwise stick with `slim` — the 100MB savings is rarely worth the compatibility debugging.

### Why Non-Root User

Running as `root` inside a container means:

- Container escape vulnerabilities grant root on the host
- Any file-write bug can modify system files
- Violates the principle of least privilege

```dockerfile
# Create user with no shell, no home directory
RUN groupadd -r appuser && useradd -r -g appuser -s /sbin/nologin appuser
USER appuser
```

This is a **security requirement**, not a best practice.

---

## 3. Uvicorn Production Configuration

### The Flags That Matter

```bash
uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \
    --loop uvloop \
    --http httptools \
    --log-level warning \
    --limit-concurrency 100 \
    --backlog 2048 \
    --timeout-keep-alive 5
```

### What Each Flag Does

| Flag | Purpose | Default | Production Value |
|------|---------|---------|-----------------|
| `--host 0.0.0.0` | Bind to all interfaces (required in Docker) | `127.0.0.1` | `0.0.0.0` |
| `--port 8000` | Listen port | `8000` | `8000` |
| `--workers N` | Number of OS processes | `1` | ~`1 per CPU core` (async workers — see note below) |
| `--loop uvloop` | Faster event loop implementation | `auto` | `uvloop` |
| `--http httptools` | Faster HTTP parser | `auto` | `httptools` |
| `--log-level warning` | Suppress per-request access logs | `info` | `warning` or `info` |
| `--limit-concurrency N` | Max in-flight requests per worker | `None` (unlimited) | Set based on load testing |
| `--backlog N` | TCP connection queue size | `2048` | `2048` |
| `--timeout-keep-alive N` | Idle connection timeout (seconds) | `5` | `5` |

### Understanding `--workers`

Uvicorn's `--workers` flag spawns multiple **processes**, each with its own event loop.

```
uvicorn --workers 4

Parent process (supervisor)
  ├── Worker 1 (event loop + app)
  ├── Worker 2 (event loop + app)
  ├── Worker 3 (event loop + app)
  └── Worker 4 (event loop + app)
```

Each worker is a fully independent copy of your application. They share nothing in memory.

> **Worker count for async workers is not `2 * CPU + 1`.** That formula is Gunicorn's heuristic for *synchronous* workers, where one process handles one request at a time. An async Uvicorn worker handles many concurrent requests on a single event loop, so you don't need extra processes to hide I/O latency. Start at roughly **one worker per CPU core** and tune from load tests. (For purely I/O-bound workloads a single worker per core is often plenty; more workers mainly help saturate multiple cores for CPU-bound work.)

### Understanding `--limit-concurrency`

This is your **pod-level admission control**.

```
--limit-concurrency 100

Request 1-100   → accepted, processed by event loop
Request 101     → HTTP 503 immediately
```

Without this, a burst of 10,000 requests all enter the event loop simultaneously, memory spikes, and the pod crashes.

> **Always set `--limit-concurrency` in production.** The correct value depends on your workload — start with 100 and adjust based on load testing.

### Install Performance Dependencies

```
pip install uvloop httptools
```

These are C-based replacements for Python's default asyncio loop and HTTP parser. Typical improvement: **2-3x throughput** for no code changes.

---

## 4. Gunicorn + Uvicorn

### When to Use Gunicorn

Uvicorn can run multiple workers on its own. Gunicorn adds:

- **Mature process management** — worker recycling, graceful restarts
- **Worker health monitoring** — restarts workers that hang or crash
- **Configurable worker lifecycle** — `max-requests` to prevent memory leaks
- **Graceful reloads** — `SIGHUP` for zero-downtime config changes

### The Decision

| Scenario | Recommendation |
|----------|---------------|
| Kubernetes (one process per pod) | Uvicorn alone |
| Single VM, multiple workers | Gunicorn + UvicornWorker |
| Docker Compose, no orchestrator | Gunicorn + UvicornWorker |
| Development | Uvicorn with `--reload` |

> **In Kubernetes, run one Uvicorn worker per container.** Let Kubernetes handle process management, scaling, and health checks. Running Gunicorn inside Kubernetes adds a redundant management layer.

### Gunicorn + UvicornWorker Configuration

> **Use the `uvicorn-worker` package, not `uvicorn.workers`.** Since Uvicorn 0.30 the bundled `uvicorn.workers.UvicornWorker` is deprecated and emits a warning; the worker now lives in the separate [`uvicorn-worker`](https://pypi.org/project/uvicorn-worker/) package as `uvicorn_worker.UvicornWorker`. Add `uvicorn-worker` to your dependencies.

```bash
gunicorn app.main:app \
    --worker-class uvicorn_worker.UvicornWorker \
    --workers 4 \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests 10000 \
    --max-requests-jitter 1000 \
    --access-logfile - \
    --error-logfile -
```

### What Each Flag Does

| Flag | Purpose |
|------|---------|
| `--worker-class uvicorn_worker.UvicornWorker` | Use Uvicorn as the ASGI worker (from the `uvicorn-worker` package) |
| `--workers 4` | Number of worker processes |
| `--timeout 120` | Kill worker if it doesn't respond in 120s |
| `--graceful-timeout 30` | Time for graceful shutdown per worker |
| `--max-requests 10000` | Recycle worker after N requests (prevents memory leaks) |
| `--max-requests-jitter 1000` | Randomize restart point (prevents all workers restarting at once) |

### Dockerfile with Gunicorn

```dockerfile
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd -r appuser && useradd -r -g appuser -s /sbin/nologin appuser

COPY ./app ./app

USER appuser

EXPOSE 8000

CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn_worker.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--max-requests", "10000", \
     "--max-requests-jitter", "1000"]
```

---

## 5. Docker Compose for Development

### Full Stack: App + Postgres + Redis

```yaml
# docker-compose.yml
# Compose V2 ignores the top-level `version:` key (and warns if present) —
# omit it.

services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/appdb
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./app:/app/app  # Hot reload in development
    command: >
      uvicorn app.main:app
      --host 0.0.0.0
      --port 8000
      --reload
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: appdb
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  postgres_data:
  redis_data:
```

### Key Details

**`depends_on` with health checks**: Without `condition: service_healthy`, Docker Compose only waits for the container to _start_, not for the service inside it to be _ready_. Your app will crash trying to connect to a Postgres that hasn't finished initialization.

**Volume mount for hot reload**: `./app:/app/app` maps your local code into the container so `--reload` picks up changes. Remove this in production.

**Named volumes for data persistence**: `postgres_data` and `redis_data` survive container restarts. Without them, you lose all data on `docker-compose down`.

---

## 6. `.dockerignore`

### Why It Matters

Without `.dockerignore`, `COPY . .` sends **everything** to the Docker daemon — git history, virtual environments, IDE files, test data.

```
# .dockerignore

# Version control
.git
.gitignore

# Python
__pycache__
*.pyc
*.pyo
.venv
venv
env

# IDE
.vscode
.idea
*.swp
*.swo

# Testing
.pytest_cache
.coverage
htmlcov
.tox

# Docker
docker-compose*.yml
Dockerfile*
.dockerignore

# Environment / secrets
.env
.env.*
*.pem
*.key

# Documentation
*.md
docs/
LICENSE

# OS
.DS_Store
Thumbs.db

# Build artifacts
dist/
build/
*.egg-info

# uv
.python-version
```

### Impact

| Without `.dockerignore` | With `.dockerignore` |
|------------------------|---------------------|
| Build context: 500MB+ (includes `.git`) | Build context: 5MB (code + requirements) |
| Build time: 30s+ | Build time: 5s |
| Secrets potentially in image | Secrets excluded |
| Cache invalidated by irrelevant changes | Cache stable |

---

## 7. Health Checks

### Two Endpoints, Two Purposes

| Endpoint | Question | Used By | Action on Failure |
|----------|----------|---------|-------------------|
| `/health` (liveness) | Is the process alive? | Docker, Kubernetes | Restart container |
| `/ready` (readiness) | Can it handle traffic? | Load balancer, Kubernetes | Stop sending traffic |

### Implementation

```python
from fastapi import FastAPI, Response
import asyncpg
import redis.asyncio as redis

app = FastAPI()


@app.get("/health")
async def health():
    """Liveness: is the process running and responsive?

    Keep this simple. No external dependency checks.
    If this fails, the container gets restarted.
    """
    return {"status": "alive"}


@app.get("/ready")
async def readiness(response: Response):
    """Readiness: can this instance handle traffic?

    Check critical dependencies. If any fail,
    this instance should be removed from the load balancer.
    """
    checks = {}

    # Check database
    try:
        await app.state.db_pool.fetchval("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    # Check Redis
    try:
        await app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    all_healthy = all(v == "ok" for v in checks.values())

    if not all_healthy:
        response.status_code = 503

    return {"status": "ready" if all_healthy else "degraded", "checks": checks}
```

### Common Mistake: Liveness Checks That Call Databases

```python
# ❌ WRONG — liveness check depends on external service
@app.get("/health")
async def health():
    await db.execute("SELECT 1")     # If DB is down, container restarts
    await redis.ping()               # Restarting won't fix a DB outage
    return {"status": "ok"}
```

```python
# ✅ CORRECT — liveness is self-contained, readiness checks dependencies
@app.get("/health")
async def health():
    return {"status": "alive"}

@app.get("/ready")
async def ready():
    # Check dependencies here
    ...
```

If liveness depends on the database and the database goes down, **every container restarts in a loop**. The database comes back to a thundering herd of reconnections. This turns a database blip into a full outage.

### Docker HEALTHCHECK

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
```

| Parameter | Meaning |
|-----------|---------|
| `--interval` | Time between checks |
| `--timeout` | Max time for a single check |
| `--start-period` | Grace period during startup (failures don't count) |
| `--retries` | Consecutive failures before marking unhealthy |

> **`--start-period` is critical.** Without it, a slow-starting app (running migrations, loading ML models) gets killed before it finishes starting.

---

## 8. Graceful Shutdown

### The Problem

When a container stops, Docker sends `SIGTERM`, waits 10 seconds (default), then sends `SIGKILL`.

If your app doesn't handle `SIGTERM`:

- In-flight requests are dropped mid-response
- Database transactions are left open
- WebSocket connections break without close frames
- Background tasks vanish

### How Uvicorn Handles Signals

Uvicorn already handles `SIGTERM` and `SIGINT`:

1. Receives `SIGTERM`
2. Stops accepting new connections
3. Waits for in-flight requests to complete
4. Calls ASGI lifespan shutdown
5. Exits

### FastAPI Lifespan for Resource Cleanup

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx
import asyncpg


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- Startup ----
    app.state.db_pool = await asyncpg.create_pool(
        dsn="postgresql://user:pass@db:5432/appdb",
        min_size=5,
        max_size=20,
    )
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    app.state.redis = redis.from_url("redis://redis:6379/0")

    yield  # Application runs here

    # ---- Shutdown ----
    # Close in reverse order of creation
    await app.state.redis.close()
    await app.state.http_client.aclose()
    await app.state.db_pool.close()


app = FastAPI(lifespan=lifespan)
```

### Docker Stop Timeout

```yaml
# docker-compose.yml
services:
  app:
    stop_grace_period: 30s  # Give app 30s to finish, not just 10s
```

```bash
# Or via CLI
docker stop --time 30 my_container
```

### The PID 1 Problem

In Docker, your application **must** run as PID 1 to receive signals.

```dockerfile
# ❌ WRONG — shell wraps the process, eats signals
CMD uvicorn app.main:app --host 0.0.0.0

# This actually runs: /bin/sh -c "uvicorn app.main:app --host 0.0.0.0"
# SIGTERM goes to sh, not uvicorn
```

```dockerfile
# ✅ CORRECT — exec form, uvicorn is PID 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

The **exec form** (JSON array) runs the process directly without a shell wrapper. The **shell form** (plain string) wraps your command in `/bin/sh -c`, which intercepts signals.

### Connection Draining in Practice

```python
import signal
import asyncio
from fastapi import FastAPI

app = FastAPI()

# Track active connections for observability
active_requests = 0


@app.middleware("http")
async def track_requests(request, call_next):
    global active_requests
    active_requests += 1
    try:
        response = await call_next(request)
        return response
    finally:
        active_requests -= 1


@app.get("/debug/connections")
async def connection_count():
    """Useful during shutdown debugging."""
    return {"active_requests": active_requests}
```

---

## 9. Environment Variables in Docker

### Three Mechanisms, Three Purposes

| Mechanism | Set at | Baked into image | Use case |
|-----------|--------|-----------------|----------|
| `ENV` in Dockerfile | Build time | Yes | Defaults, PATH, Python settings |
| `ARG` in Dockerfile | Build time | No (not in final image) | Build-time configuration |
| `environment` / `env_file` in Compose | Run time | No | Secrets, per-environment config |

### Dockerfile: `ENV` vs `ARG`

```dockerfile
# ARG — only available during build, not in running container
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

# ENV — available in running container
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
```

### Recommended Environment Variables

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
```

| Variable | Purpose |
|----------|---------|
| `PYTHONDONTWRITEBYTECODE=1` | Don't create `.pyc` files (no point in containers) |
| `PYTHONUNBUFFERED=1` | Force stdout/stderr to be unbuffered (logs appear immediately) |

### Docker Compose: Runtime Configuration

```yaml
# docker-compose.yml
services:
  app:
    # Method 1: Inline (for non-sensitive values)
    environment:
      - APP_ENV=production
      - LOG_LEVEL=warning
      - WORKERS=4

    # Method 2: File (for secrets and many variables)
    env_file:
      - .env
```

```bash
# .env file (NEVER commit this)
DATABASE_URL=postgresql+asyncpg://user:secretpass@db:5432/appdb
REDIS_URL=redis://redis:6379/0
SECRET_KEY=your-secret-key-here
API_KEY=sk-abc123
```

### Reading Config in FastAPI

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    redis_url: str
    secret_key: str
    debug: bool = False
    workers: int = 4
    log_level: str = "warning"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
```

### Anti-Pattern: Hardcoded Secrets

```python
# ❌ WRONG — secrets baked into image
ENV DATABASE_URL=postgresql://user:realpassword@prod-db:5432/app
```

```python
# ✅ CORRECT — secrets injected at runtime
# Dockerfile: no secrets
# docker-compose.yml or Kubernetes: inject via env_file or secrets
```

---

## 10. Common Patterns

### Package Managers: pip vs Poetry vs uv

#### `requirements.txt` + pip — Simple and Universal

No extra tooling. Works everywhere.

```dockerfile
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```

#### Poetry — Lock File for Reproducibility

Requires Poetry installed in the build stage.

```dockerfile
# ---- Build stage ----
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir poetry

WORKDIR /build
COPY pyproject.toml poetry.lock ./

# Export to requirements.txt, then install normally
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime stage ----
FROM python:3.12-slim

COPY --from=builder /install /usr/local
COPY ./app /app/app
```

> **Do not install Poetry in the runtime image.** Export to `requirements.txt` in the build stage and use plain `pip` in the runtime stage. Poetry is a build tool, not a runtime dependency.

#### uv — Fast, Modern, Recommended

[uv](https://github.com/astral-sh/uv) is a Python package manager written in Rust by Astral (the makers of Ruff). It is **10-100x faster** than pip and Poetry, has a built-in lockfile (`uv.lock`), and is designed for Docker from the ground up.

```dockerfile
# ---- Build stage ----
FROM python:3.13-slim AS builder

# Copy uv binary directly from the official image — no pip install needed
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./

# Install dependencies only (no project itself yet)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy source and install the project
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- Runtime stage ----
FROM python:3.13-slim

WORKDIR /app
COPY --from=builder /app /app

# uv creates a .venv inside /app — just put it on PATH
ENV PATH="/app/.venv/bin:$PATH"
```

**What each flag does:**

| Flag / Setting | Purpose |
|---|---|
| `COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv` | Copies the uv binary from the official image. No pip install needed — it's a single static binary |
| `UV_COMPILE_BYTECODE=1` | Pre-compile `.pyc` files during install. Faster cold start (no compilation at runtime) |
| `UV_LINK_MODE=copy` | Copy files into `.venv` instead of symlinking. Required for multi-stage builds where the source layer won't exist |
| `--frozen` | Use `uv.lock` exactly as-is. Fails if lockfile is outdated (guarantees reproducibility) |
| `--no-install-project` | Install only dependencies, not the project itself. Used in the first `RUN` to cache deps separately from source |
| `--no-dev` | Skip dev dependencies (pytest, mypy, etc.) |
| `--mount=type=cache,target=/root/.cache/uv` | Docker BuildKit cache mount. uv's cache persists across builds — repeat builds install in <1s |

**The two-step install pattern** is the key Docker optimization:

```
Step 1: COPY pyproject.toml uv.lock → uv sync --no-install-project
         (cached unless dependencies change)

Step 2: COPY src/ → uv sync
         (only re-runs when source code changes, deps already cached)
```

This is the same principle as the pip layer ordering trick (copy requirements before source), but uv makes it explicit with `--no-install-project`.

### Comparison: pip vs Poetry vs uv

| Concern | pip | Poetry | uv |
|---|---|---|---|
| Speed (clean install) | Baseline | ~1.5x slower | **10-100x faster** |
| Lockfile | ❌ (manual `pip freeze`) | ✅ `poetry.lock` | ✅ `uv.lock` |
| Reproducibility | Low (no lock) | High | High |
| Docker cache support | Manual layer ordering | Export to requirements.txt | Native (`--mount=type=cache`) |
| Binary size in build | Already present | ~30MB install | ~15MB single binary (COPY from image) |
| Installs in runtime image | No (use `--prefix`) | No (export + pip) | No (COPY `.venv`) |
| Compile bytecode | ❌ | ❌ | ✅ `UV_COMPILE_BYTECODE=1` |

> **If you're starting a new project, use uv.** It's faster, simpler in Docker, and produces reproducible builds out of the box. For existing projects on pip or Poetry, migrating is straightforward — `uv` can read `requirements.txt` and `pyproject.toml` directly.

### `pip install --no-cache-dir`

```bash
# ❌ Without --no-cache-dir
pip install -r requirements.txt
# Leaves ~100MB+ of cached wheels in /root/.cache/pip

# ✅ With --no-cache-dir
pip install --no-cache-dir -r requirements.txt
# No cache written, smaller image
```

Every MB multiplies across every pull, node, and deployment.

### Layer Ordering for Cache

Docker caches each layer. If a layer changes, **all subsequent layers are rebuilt**.

```dockerfile
# ❌ WRONG — changing any source file invalidates pip install cache
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
```

```dockerfile
# ✅ CORRECT — requirements change rarely, source code changes often
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ./app ./app
```

With the correct ordering, `pip install` is cached as long as `requirements.txt` doesn't change. This turns a 60-second build step into a 0-second cache hit.

---

## 11. Common Mistakes

### Mistake 1: Running as Root

```dockerfile
# ❌ Default — container runs as root
FROM python:3.12-slim
COPY . .
CMD ["uvicorn", "app.main:app"]
```

```dockerfile
# ✅ Create and use non-root user
FROM python:3.12-slim
RUN groupadd -r appuser && useradd -r -g appuser appuser
COPY . .
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]
```

### Mistake 2: Using Full Base Image

```dockerfile
# ❌ 1 GB image with gcc, make, man pages
FROM python:3.12

# ✅ 150 MB image with only what's needed
FROM python:3.12-slim
```

### Mistake 3: Missing `.dockerignore`

```
# Without .dockerignore:
Sending build context to Docker daemon  450MB   # includes .git, .venv, node_modules

# With .dockerignore:
Sending build context to Docker daemon  4.2MB   # just code and requirements
```

### Mistake 4: `COPY .` Before `pip install` (Cache Busting)

```dockerfile
# ❌ Every code change triggers a full pip install (60+ seconds)
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

# ✅ pip install is cached unless requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ./app ./app
```

### Mistake 5: No `PYTHONUNBUFFERED`

```dockerfile
# ❌ Logs are buffered — you see nothing during crashes
FROM python:3.12-slim

# ✅ Logs appear immediately
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
```

Without this, Python buffers stdout. When your container crashes, the last log lines are lost because they're still in the buffer.

### Mistake 6: Shell Form CMD

```dockerfile
# ❌ Shell form — signals go to /bin/sh, not your app
CMD uvicorn app.main:app --host 0.0.0.0

# ✅ Exec form — your app receives signals directly
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Mistake 7: No Health Check

```dockerfile
# ❌ Docker has no idea if your app is healthy
FROM python:3.12-slim
CMD ["uvicorn", "app.main:app"]

# ✅ Docker can detect and restart unhealthy containers
FROM python:3.12-slim
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]
```

### Mistake 8: Storing Secrets in `ENV`

```dockerfile
# ❌ Secrets visible in image history (docker history)
ENV API_KEY=sk-live-abc123
ENV DATABASE_PASSWORD=supersecret

# ✅ Inject at runtime via env_file or orchestrator secrets
# Dockerfile has no secrets at all
```

Anyone with access to the image can run `docker history` and see every `ENV` instruction.

---

## 12. Complete Production Dockerfiles

### Option A: pip-based (Traditional)

```dockerfile
# ============================================================
# Stage 1: Build dependencies
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ============================================================
# Stage 2: Production runtime
# ============================================================
FROM python:3.12-slim AS runtime

# Runtime-only system dependency for psycopg2
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Non-root user
RUN groupadd -r appuser && useradd -r -g appuser -s /sbin/nologin appuser

# Copy application code (last layer — changes most often)
COPY ./app ./app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--loop", "uvloop", \
     "--http", "httptools", \
     "--log-level", "warning", \
     "--limit-concurrency", "100", \
     "--timeout-keep-alive", "5"]
```

### Option B: uv-based (Recommended)

```dockerfile
# ============================================================
# Base: shared between builder and runner
# ============================================================
FROM python:3.13-slim-bookworm AS base

WORKDIR /app

# ============================================================
# Stage 1: Build dependencies with uv
# ============================================================
FROM base AS builder

# Single static binary — no pip install, no version conflicts
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Step 1: Install dependencies only (cached unless pyproject.toml/uv.lock change)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Step 2: Copy source and install the project
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ============================================================
# Stage 2: Production runtime (no uv, no build tools)
# ============================================================
FROM base AS runner

# Copy the entire /app (includes .venv with all dependencies)
COPY --from=builder /app /app

# .venv/bin has python, uvicorn, gunicorn — put it on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Non-root user
RUN groupadd -r app \
 && useradd -m -r -g app -d /home/app app \
 && chown -R app:app /home/app /app

ENV HOME=/home/app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--loop", "uvloop", \
     "--http", "httptools", \
     "--log-level", "warning", \
     "--limit-concurrency", "100", \
     "--timeout-keep-alive", "5"]
```

**Why uv produces smaller, faster builds:**

| Metric | pip Dockerfile | uv Dockerfile |
|---|---|---|
| Build time (clean) | 60-90s | 5-10s |
| Build time (deps cached) | 0s (layer cache) | 0s (layer cache + uv cache) |
| Build time (source change only) | 0s | <1s (only re-links project) |
| Needs gcc/build tools | Yes (for C extensions) | Only if package has C extensions |
| Bytecode pre-compiled | No (compiled on first import) | Yes (`UV_COMPILE_BYTECODE=1`) |
| Runtime image has package manager | Yes (pip in stdlib) | No (uv only in builder) |

> **Note on Gunicorn:** If you're running behind Gunicorn (recommended for non-Kubernetes deployments), replace the `CMD` with:
> ```dockerfile
> CMD ["gunicorn", "-k", "uvicorn_worker.UvicornWorker", \
>      "-w", "4", "-b", "0.0.0.0:8000", \
>      "--access-logfile", "-", \
>      "--max-requests", "10000", \
>      "--max-requests-jitter", "1000", \
>      "app.main:app"]
> ```

---

## 13. Summary

### Layer Responsibilities

| Layer | Decides | Configured By |
|-------|---------|---------------|
| Dockerfile | What's in the image | Developer |
| Uvicorn | How the ASGI server behaves | Flags or env vars |
| Gunicorn | Process management and recycling | Config file or flags |
| Docker Compose | How services connect | `docker-compose.yml` |
| Health checks | Is this instance usable | `/health` + `/ready` endpoints |
| Graceful shutdown | How to stop without data loss | Lifespan + signal handling |

### Quick Reference

```bash
# Build
docker build -t myapp:latest .

# Run
docker run -p 8000:8000 --env-file .env myapp:latest

# Development
docker compose up --build

# Production (single VM)
docker compose -f docker-compose.prod.yml up -d

# Check health
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

### The Key Insight

> **A container is not a VM. It is a process with isolated filesystem and network.**

Treat it that way: small images, single process, no state, fast startup, clean shutdown. Everything else — scaling, failover, routing — is the orchestrator's job.

---

## Kubernetes Essentials for Python Services

Deployment mechanics this corpus didn't cover elsewhere. Short and pragmatic; not a Kubernetes tutorial.

### Rolling Deployments vs Blue/Green

**Rolling update (Kubernetes default):** new pods are created while old ones are terminated, one or a few at a time. No downtime, but old and new versions run simultaneously for a few seconds to minutes.

```yaml
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 25%          # at most 125% of desired replicas during rollout
      maxUnavailable: 0      # never drop below 100% availability
```

**Blue/Green:** deploy the new version to a parallel environment ("green"), run smoke tests, then flip the load balancer. Instant cutover, instant rollback.

- Rolling: cheaper (no doubled capacity), simpler to operate, gives you canary-like gradual exposure.
- Blue/green: cleaner rollback (just flip traffic back), but doubles the running cost during cutover, and database migrations become harder (both versions must work against the migrated schema).

**When to use which:**
- Stateless services with compatible schema migrations: rolling.
- Services with risky migrations or where "old and new running at the same time" is unsafe: blue/green.
- Most teams: rolling with good observability + manual canary if the change is sensitive.

### Readiness vs Liveness Probes — Tuning That Matters

Two probes, two different questions:

- **Liveness**: "is this process alive, or is it stuck/deadlocked and needs a kill?" Failing liveness → pod is killed and restarted.
- **Readiness**: "should this pod receive traffic right now?" Failing readiness → pod is removed from the Service's endpoints, but **not** killed.

For a Python web service, liveness is almost always more dangerous than useful — a slow response is not a dead process, but a wrong liveness probe will kill a busy pod mid-request. Rule of thumb:

- **Liveness**: very conservative. Only fail on a condition that genuinely means "this pod will never recover without a restart" — deadlocked event loop, stuck forever on a corrupt internal state.
- **Readiness**: tighter. This pod is temporarily unable to serve — dependency down, startup not complete, queue drain in progress.

```yaml
containers:
- name: api
  readinessProbe:
    httpGet: { path: /ready, port: 8000 }
    initialDelaySeconds: 5       # don't probe before the app can possibly be up
    periodSeconds: 5
    timeoutSeconds: 2
    failureThreshold: 3          # remove from service after 15s of failure
  livenessProbe:
    httpGet: { path: /health, port: 8000 }
    initialDelaySeconds: 30      # generous — app must be fully warm first
    periodSeconds: 15
    timeoutSeconds: 5
    failureThreshold: 6          # kill only after 90s of failure
  # startupProbe (optional) — use when startup is slow (>30s)
  startupProbe:
    httpGet: { path: /health, port: 8000 }
    periodSeconds: 5
    failureThreshold: 60         # gives 5 minutes to finish starting
```

Interaction with graceful shutdown:
- On termination, Kubernetes sends SIGTERM, then waits `terminationGracePeriodSeconds` (default 30s) before SIGKILL.
- **Readiness check must start failing *before* SIGTERM arrives** so traffic stops before in-flight requests wind down. Implement a "draining" mode in your app that flips a flag when shutdown begins, which your `/ready` endpoint reads.
- Match `preStop` hooks to avoid races: `sleep 5` before shutdown gives the LB time to stop sending traffic.

### Resource Requests and Limits

Every pod spec should set these — leaving them unset is one of the most common causes of noisy-neighbor problems in clusters.

```yaml
resources:
  requests:
    cpu: 250m          # 0.25 CPU — guarantees scheduling on a node with this available
    memory: 512Mi      # guarantees this much RAM
  limits:
    cpu: 1000m         # 1 CPU — hard cap; beyond this, the pod is throttled
    memory: 1Gi        # hard cap; beyond this, the pod is OOM-killed
```

- **Requests** = what the scheduler reserves. Too low → pod co-locates with neighbors and fights for CPU/memory. Too high → wastes cluster capacity.
- **Limits** = hard ceiling. CPU: you get throttled (latency spike, not a kill). Memory: you get OOM-killed.
- **Memory limit == memory request** is safe and predictable. **CPU limit > CPU request** lets you burst when a neighbor isn't using theirs.
- Measure under load. Pick requests at p50 steady-state, limits at p99 peak + headroom. Adjust based on actual metrics, not guesses.

**Python-specific note:** Python doesn't release memory back to the OS easily. If the process's high-water mark is 700MB but it's currently using 400MB, the OS reports 700MB. Set memory limits accounting for this.

### Autoscaling — HPA and KEDA

**Horizontal Pod Autoscaler (HPA):** scales pod count based on CPU or custom metrics.

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: { name: api }
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: api }
  minReplicas: 2
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target: { type: Utilization, averageUtilization: 70 }
```

HPA reacts on a 15–30s cycle and has configurable scale-up / scale-down stabilization windows. Default scale-down is conservative (5 minutes) to avoid flapping. Don't touch it without a reason.

**KEDA:** HPA extended with event-driven sources — Redis queue depth, Kafka lag, SQS message count, HTTP requests per second via Prometheus. Essential when your service is queue-driven (background workers), not request-driven.

```yaml
# KEDA ScaledObject scaling on Redis list length
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata: { name: worker }
spec:
  scaleTargetRef: { kind: Deployment, name: worker }
  minReplicaCount: 1
  maxReplicaCount: 50
  triggers:
  - type: redis
    metadata:
      address: redis.default:6379
      listName: dramatiq:default
      listLength: "10"     # target: one pod per ~10 messages queued
```

KEDA can scale to zero, which HPA cannot — useful for low-traffic workers. Pair with a cold-start-tolerant workload.

**Autoscaling gotchas:**
- Scaling on CPU alone is blunt. A queue-driven worker that's I/O-waiting isn't using CPU even when backed up. Scale on queue depth or response latency instead.
- Scale-up is limited by pod startup time. A 60-second startup means you can't react to 30-second traffic spikes; pre-scale via scheduled scaling or keep a baseline of minReplicas.
- Scale-down can kill in-flight requests. Combine with proper readiness probes + graceful shutdown.

---

## See also

- [../testing/08_database_testing.md](../testing/08_database_testing.md) — integration tests with real Postgres via testcontainers.
- [../../fundamentals/fastapi/Safe_and_Scalable_API_calls/04_kubernetes.md](../../fundamentals/fastapi/Safe_and_Scalable_API_calls/04_kubernetes.md) — per-pod vs cluster-wide admission control.
