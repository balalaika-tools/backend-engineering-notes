# Part 5: Complete Production Architecture

> **Principle**: Each layer has a non-overlapping responsibility.

---

## 1. High-Level Architecture

```
Internet
  ↓
API Gateway / Ingress Controller
  ↓
Kubernetes Service (Load Balancer)
  ↓
Readiness Checks
  ↓
ASGI Server (Uvicorn) — Pod Admission Control
  ↓
FastAPI Application
  ├─ User/Endpoint Rate Limiting (Redis)
  ├─ Business Logic
  └─ External Vendor Call Pipeline
      ├─ Global Vendor Rate Limiter (Redis)
      ├─ Local Rate Limiter (burst smoothing)
      ├─ Semaphore (capacity)
      └─ HTTP Client
  ↓
External Vendor APIs
```

---

## 2. Layer 1: API Gateway / Ingress Controller

### Role

Protect the **entire cluster** from excessive or malicious inbound traffic.

### Scope

Cluster-wide, **before traffic reaches any pod**.

### Responsibilities

| Mechanism | Purpose |
|-----------|---------|
| Global RPS limit | Total traffic ceiling |
| Per-IP rate limit | Prevent single-client floods |
| Connection limits | Prevent socket exhaustion |
| Bounded buffering | No unlimited queuing |

### Example (Nginx Ingress)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  annotations:
    nginx.ingress.kubernetes.io/limit-rps: "2000"
    nginx.ingress.kubernetes.io/limit-connections: "5000"
    nginx.ingress.kubernetes.io/limit-rpm: "100"  # per IP
```

### What Gateway Does NOT Do

❌ User-level fairness
❌ Business rules (tenant quotas)
❌ Vendor API protection
❌ Application-level logic

> Gateway protects **infrastructure**, not business logic.

---

## 3. Layer 2: Readiness and Health Checks

### Role

Route traffic **only to healthy pods**.

### How It Works

1. Each pod exposes `/ready` endpoint
2. Kubernetes probes periodically
3. If fails → pod removed from service
4. Traffic shifts to healthy pods

### FastAPI Readiness Endpoint

```python
@app.get("/ready")
async def readiness():
    """Can this pod handle traffic?"""
    
    # Check semaphore saturation
    if llm_sem.locked() and queue_size() > 80:
        return Response(status_code=503, content='{"status": "overloaded"}')
    
    # Check error rate
    if recent_error_rate() > 0.5:
        return Response(status_code=503, content='{"status": "high errors"}')
    
    return {"status": "ready"}
```

### Kubernetes Configuration

```yaml
readinessProbe:
  httpGet:
    path: /ready
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 3
```

### Liveness vs Readiness

| Probe | Question | Action if fails |
|-------|----------|-----------------|
| Liveness | Is the process alive? | Restart pod |
| Readiness | Can it handle traffic? | Remove from service |

---

## 4. Layer 3: ASGI Server — Pod Admission Control

### Role

"Should this pod accept another request right now?"

### Scope

Per-pod, **hard capacity protection**.

This happens **before FastAPI code runs**.

### Uvicorn Configuration

```bash
uvicorn app:app \
  --host 0.0.0.0 \
  --port 8000 \
  --limit-concurrency 100 \
  --backlog 200 \
  --timeout-keep-alive 5
```

| Flag | Purpose |
|------|---------|
| `--limit-concurrency` | Max in-flight requests per worker |
| `--backlog` | Queued connections before refusal |
| `--timeout-keep-alive` | How long to keep idle connections open |

### Understanding `--timeout-keep-alive`

**What it is**: How long (in seconds) Uvicorn keeps an idle HTTP connection open waiting for another request.

**HTTP Keep-Alive recap**:
- HTTP/1.1 reuses connections by default
- Client sends request → server responds → connection stays open
- Client can send another request without new TCP handshake

**The `--timeout-keep-alive` setting**:

```
Client sends request
  ↓
Server responds
  ↓
Connection idle, timer starts (5s default)
  ↓
If no new request within 5s → connection closed
If new request arrives → timer resets
```

**Why 5 seconds is a good default**:
- Long enough for typical request bursts
- Short enough to reclaim resources from abandoned connections
- Prevents connection pool exhaustion from idle clients

**When to adjust**:
- **Lower (2-3s)**: High traffic, many clients, resource constrained
- **Higher (10-15s)**: Long-polling patterns, clients make periodic requests

### Why This Matters

Without admission control, a burst can overwhelm the pod:

```
Rate limit: 100 req/min
At t=0: all 100 requests arrive simultaneously
Without admission control:
  → 100 async tasks start
  → Pod memory spikes
  → Event loop blocked
```

---

## 5. Layer 4: FastAPI — User-Level Fairness

### Role

Enforce fairness based on **application logic**.

### Scope

Shared across pods via **Redis**.

### User Rate Limiting

```python
from limits import RateLimitItemPerMinute
from limits.storage import RedisStorage
from limits.strategies import FixedWindowRateLimiter

storage = RedisStorage("redis://redis:6379")
limiter = FixedWindowRateLimiter(storage)

RATE_LIMITS = {
    "free": RateLimitItemPerMinute(10),
    "pro": RateLimitItemPerMinute(100),
    "enterprise": RateLimitItemPerMinute(1000),
}


async def check_user_rate_limit(user_id: str, tier: str):
    limit = RATE_LIMITS.get(tier, RATE_LIMITS["free"])
    
    if not limiter.hit(limit, f"user:{user_id}"):
        raise HTTPException(429, f"Rate limit exceeded for tier: {tier}")
```

### Per-Endpoint Rate Limiting

```python
ENDPOINT_LIMITS = {
    "/chat": RateLimitItemPerMinute(20),
    "/search": RateLimitItemPerMinute(100),
    "/analyze": RateLimitItemPerMinute(5),
}
```

**Why at application level?**

- Gateway doesn't know user identity
- Tier-based limits require database lookups
- Business rules live in application code

---

## 6. Layer 5: Outbound Vendor Protection

### The Problem

**Inbound limits do not protect vendors.**

Vendor limits are:
- Global (shared across all pods)
- API-key / account scoped
- Strictly enforced by vendor

### Global Vendor Rate Limiter (Redis)

Every outbound vendor call **must pass through this**.

```python
import asyncio
import httpx
from aiolimiter import AsyncLimiter
from fastapi import HTTPException

# === GLOBAL PRIMITIVES (initialized at startup) ===
llm_sem = asyncio.Semaphore(50)
llm_rate_local = AsyncLimiter(80, 60)
client: httpx.AsyncClient = None
vendor_limiter: GlobalVendorRateLimiter = None  # See Part 4 for implementation


def backoff(attempt: int) -> float:
    import random
    return min(2 ** attempt, 32) + random.uniform(0, 1)


async def call_llm(payload: dict):
    for attempt in range(3):
        try:
            async with asyncio.timeout(5):  # Queue timeout
                async with llm_rate_local:  # Local smoothing
                    
                    # GLOBAL VENDOR PROTECTION
                    await vendor_limiter.wait_and_acquire(timeout=3.0)
                    
                    async with llm_sem:  # Local capacity
                        async with asyncio.timeout(30):  # Call timeout
                            response = await client.post(
                                "https://api.openai.com/v1/chat/completions",
                                json=payload,
                            )
                            response.raise_for_status()
                            return response.json()
        
        except VendorRateLimitExceeded:
            if attempt < 2:
                await asyncio.sleep(backoff(attempt))
                continue
            raise HTTPException(429, "Vendor rate limit exceeded")
        
        except httpx.TimeoutException:
            if attempt < 2:
                await asyncio.sleep(backoff(attempt))
                continue
            raise HTTPException(504, "Vendor timeout")
```

---

## 7. Complete Execution Order

The order in which controls are applied **matters**.

```
1. Incoming request
   ↓
2. API Gateway limits (flood protection)
   ↓
3. Ingress routing (readiness checks)
   ↓
4. ASGI admission control (per-pod capacity)
   ↓
5. FastAPI user/endpoint limits (Redis, fairness)
   ↓
6. Business logic / validation
   ↓
7. Queue timeout (start waiting)
   ↓
8. Local rate limiter (burst smoothing, optional)
   ↓
9. GLOBAL vendor rate limiter (Redis, vendor protection)
   ↓
10. Semaphore (local concurrency)
    ↓
11. Call timeout (per-attempt budget)
    ↓
12. HTTP client (with timeouts)
    ↓
13. Vendor API
    ↓
14. Retry logic (if needed)
```

### Why Order Matters

> **Reject before waiting. Wait before reserving. Reserve before calling.**

| Stage | Cost | Action |
|-------|------|--------|
| Gateway reject | Minimal | Fast reject |
| User limit exceeded | Cheap (Redis GET) | Fast reject |
| Queue timeout | Medium (async wait) | Wait or reject |
| Semaphore | Expensive (slot) | Reserve |
| Vendor call | Very expensive | Execute |

### Anti-Pattern

```python
# WRONG: Semaphore before checks
async with llm_sem:
    if not check_user_rate_limit():
        raise HTTPException(429)  # Wasted semaphore slot!
    await vendor_call()
```

### Correct

```python
# RIGHT: Checks before semaphore
if not check_user_rate_limit():
    raise HTTPException(429)

async with llm_sem:
    await vendor_call()
```

---

## 8. Responsibility Matrix

| Concern | Gateway | ASGI | FastAPI | Vendor Call |
|---------|---------|------|---------|-------------|
| Flood protection | ✅ | ❌ | ❌ | ❌ |
| Total RPS ceiling | ✅ | ❌ | ❌ | ❌ |
| Per-IP limits | ✅ | ❌ | ❌ | ❌ |
| Pod admission | ❌ | ✅ | ❌ | ❌ |
| Per-user fairness | ❌ | ❌ | ✅ (Redis) | ❌ |
| Endpoint limits | ❌ | ❌ | ✅ | ❌ |
| Local capacity | ❌ | ❌ | ✅ (sem) | ❌ |
| Vendor quotas | ❌ | ❌ | ❌ | ✅ (Redis) |

---

## 9. Minimal Production Setup

### Gateway

- Total RPS limit: `2000/sec`
- Per-IP limit: `100/sec`
- Connection limits: `20 per IP`

### ASGI (Uvicorn)

- `--limit-concurrency 100`
- `--backlog 200`

### FastAPI

- User rate limiter: Redis-backed
- Semaphore: `50`
- Local rate limiter: `80/min` (optional)
- Global vendor limiter: Redis-backed

### HTTP Client

```python
httpx.AsyncClient(
    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    timeout=httpx.Timeout(connect=5.0, pool=5.0, write=10.0, read=30.0),
)
```

---

## 10. Deployment Configuration

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fastapi-app
spec:
  replicas: 10
  template:
    spec:
      containers:
      - name: app
        image: myapp:latest
        command:
          - uvicorn
          - app:app
          - --host=0.0.0.0
          - --port=8000
          - --limit-concurrency=100
          - --backlog=200
        
        resources:
          requests:
            cpu: "500m"
            memory: "512Mi"
          limits:
            cpu: "1000m"
            memory: "1Gi"
        
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
        
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
          failureThreshold: 3
```

---

## 11. Shielded Cleanup

When timeouts or cancellations occur, critical cleanup operations must still run.

```python
async def call_llm_with_cleanup(payload: dict):
    try:
        async with asyncio.timeout(60):
            return await actual_llm_work(payload)
    
    except asyncio.TimeoutError:
        # Shield cleanup from cancellation
        await asyncio.shield(record_sla_violation())
        raise
```

### What MUST Go Into Shielded Cleanup

✅ Metrics recording
✅ Tracing / observability
✅ Releasing external resources
✅ Final state persistence

### What Must NOT Go Into Shielded Cleanup

❌ Retries
❌ New vendor calls
❌ Heavy computation
❌ Anything slow or unbounded

---

## 12. Final Mental Model

> **Gateway**: who may enter
> **ASGI**: can this pod accept work
> **FastAPI**: who gets how much
> **Redis vendor limiter**: when we may call vendors

Each layer protects a different resource:

- Gateway → infrastructure
- ASGI → pod
- FastAPI → users and business rules
- Vendor limiter → external contracts

---

## Summary: Complete Architecture

This architecture:

✅ Survives floods and bursts
✅ Enforces fairness
✅ Respects vendor contracts
✅ Prevents pod collapse
✅ Works with Kubernetes autoscaling
✅ Clear responsibilities
✅ No redundant limits

> **Rate limits control "how often".**
> **Admission control decides "whether at all".**

---

**Next**: [Part 6: Advanced Patterns](06_advanced_patterns.md) — circuit breakers, priority queues, load shedding.
