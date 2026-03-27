# Part 4: Kubernetes and Distributed Systems

> **Principle**: Per-pod rate limiters are process-local. Vendor limits are global.

---

## 1. The Fundamental Problem

### Scenario

Assume:
- Vendor rate limit: **500 requests / minute**
- Kubernetes pods: **10**
- Each pod uses:
  ```python
  AsyncLimiter(500, 60)
  ```

### What Happens?

Each pod independently believes it may send **500 req/min**.

**Actual cluster behavior**:

```
Total cluster rate = 10 × 500 = 5,000 req/min ❌
```

The vendor limit is **exceeded by 10x**.

### Why This Happens

There is **no shared state** between pods:
- Each pod has its own memory
- Each `AsyncLimiter` is independent
- Pods cannot "see" each other's activity

**Key fact**:

> **Per-pod rate limiters are process-local.**
> **Vendor limits are global.**

---

## 2. What "Local Fairness" Actually Means

Local fairness is **not about the vendor**.

It means:

> **Protecting a single pod from overload, even if the cluster is healthy.**

### What Local Fairness Protects

Pod-level resources:
- CPU
- Memory
- Socket pools
- Event loop responsiveness
- Tail latency
- Pod stability

### What Local Fairness Ensures

- No single pod collapses under burst traffic
- Load is smoothed locally
- Failures are fast and controlled
- Health checks continue to pass

**Mental model**:

> Local rate limiting is about **pod survivability**, not vendor protection.

---

## 3. Bursty Traffic Scenario

### Setup

Assume:
- Vendor rate limit: **500 req/min** (global)
- Each pod can safely handle: **50 concurrent outbound calls**
- Suddenly: **500 requests arrive at ONE pod**

### Without Local Limits

```
500 requests arrive
  ↓
500 async tasks start
  ↓
500 HTTP calls open
  ↓
Sockets exhausted
Memory pressure
Event loop starvation
Latency explodes
Retries amplify load
Health checks fail
Pod removed from service
  ↓
Cascading failure to other pods
```

**Even if the vendor limit is respected globally, the pod dies first.**

---

## 4. Correct Layered Handling

### Layer 1: Local Concurrency Cap (Semaphore)

```python
llm_sem = asyncio.Semaphore(50)
```

**Purpose**: "This pod will never have more than 50 in-flight vendor calls."

**Effect**:
- First 50 requests run
- Remaining 450 requests wait or fail fast
- The pod survives

This is a **capacity guard**, not a rate limit.

### Layer 2: Local Queue Timeout (Admission Control)

```python
async with asyncio.timeout(5):
    async with llm_sem:
        ...
```

**Purpose**: "If a request cannot start within 5 seconds, reject it."

**Effect**:
- No unbounded queues
- No silent latency growth
- Fast 504/429 response to client

### Layer 3: Local Rate Limiter (Fairness, NOT Vendor Protection)

**Do NOT use the vendor rate.**

Instead, base it on **pod capacity**.

#### Calculate Pod Capacity

```
Assumptions:
- Semaphore = 50
- Average vendor latency ≈ 30s

Sustainable throughput:
throughput ≈ semaphore / avg_latency
throughput ≈ 50 / 30s
throughput ≈ 1.67 req/sec
throughput ≈ 100 req/min
```

#### Set Local Rate Limiter

```python
llm_rate = AsyncLimiter(80, 60)  # 80/min with safety margin
```

**Purpose**:
- Smooths bursts
- Prevents self-overload
- Keeps latency predictable
- **NOT** for vendor protection

### What Happens to 500 Incoming Requests

With semaphore=50, local rate=80/min, queue timeout=5s:

| Requests | Outcome |
|----------|---------|
| ~50 | Start immediately |
| ~30 | Start gradually (rate-limited queue) |
| ~420 | Fail fast (queue timeout → 504/429) |

**The pod remains stable.**

---

## 5. How the Vendor Is Actually Protected

**Not by per-pod logic.**

Vendor protection requires a **global rate limiter**.

### Options

| Solution | Pros | Cons |
|----------|------|------|
| Redis-based token bucket | Precise, cluster-wide | Requires Redis |
| API Gateway rate limiting | Simple, external | Less flexible |
| Istio/Envoy rate limiting | Service mesh integration | Complex setup |

Only a **shared, cluster-wide limiter** can enforce:

```
Σ(vendor calls from all pods) ≤ vendor limit
```

---

## 6. Redis-Based Global Rate Limiter

```python
import asyncio
import redis.asyncio as redis
import time
from fastapi import HTTPException


class VendorRateLimitExceeded(Exception):
    """Raised when vendor rate limit is exceeded after waiting."""
    pass


class GlobalVendorRateLimiter:
    """
    Redis-backed global rate limiter for vendor API calls.
    Uses sliding window algorithm.
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        vendor_key: str,
        limit: int,
        window_seconds: int = 60,
    ):
        self.redis = redis_client
        self.key = f"vendor:rate:{vendor_key}"
        self.limit = limit
        self.window = window_seconds
    
    async def acquire(self) -> bool:
        """
        Try to acquire a token immediately.
        Returns True if acquired, False if rate limit exceeded.
        """
        now = time.time()
        window_start = now - self.window
        
        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(self.key, 0, window_start)
        pipe.zcard(self.key)
        results = await pipe.execute()
        
        count = results[1]
        
        if count >= self.limit:
            return False
        
        await self.redis.zadd(self.key, {f"{now}": now})
        await self.redis.expire(self.key, self.window + 10)
        
        return True
    
    async def wait_and_acquire(self, timeout: float = 5.0) -> None:
        """
        Wait up to timeout seconds to acquire a token.
        
        Why wait instead of immediate rejection?
        - Avoids socket overhead from client retries
        - Reduces connection churn
        - Smooths burst traffic
        - Often succeeds within a few hundred ms
        
        Raises:
            VendorRateLimitExceeded: if timeout exceeded without acquiring.
        """
        start = time.time()
        
        while True:
            if await self.acquire():
                return  # Success
            
            elapsed = time.time() - start
            if elapsed >= timeout:
                raise VendorRateLimitExceeded(
                    f"Could not acquire vendor token within {timeout}s"
                )
            
            # Brief wait before retry - avoids Redis hammering
            # and often succeeds as other requests complete
            await asyncio.sleep(0.1)


# === GLOBAL INITIALIZATION (at app startup) ===
redis_client: redis.Redis = None

vendor_limiter = GlobalVendorRateLimiter(
    redis_client=redis_client,  # Set during startup
    vendor_key="openai",
    limit=500,
    window_seconds=60,
)
```

### Two Approaches: Immediate vs Wait

| Approach | Use when | Behavior |
|----------|----------|----------|
| `acquire()` | Need instant decision | Returns bool immediately |
| `wait_and_acquire()` | Can tolerate brief wait | Waits up to timeout, then fails |

**Why `wait_and_acquire` is often better:**

- Client retry creates new TCP connection → expensive
- Waiting 100-200ms often succeeds as tokens become available
- Reduces overall system load
- Smoother traffic pattern

### Usage with Local Protections

```python
import asyncio
import httpx
from aiolimiter import AsyncLimiter

# === GLOBAL PRIMITIVES (created once at startup) ===
llm_sem = asyncio.Semaphore(50)
llm_rate_local = AsyncLimiter(80, 60)
client: httpx.AsyncClient = None


async def call_llm(payload: dict):
    # Global vendor protection with wait
    await vendor_limiter.wait_and_acquire(timeout=3.0)
    
    # Local capacity protection
    async with llm_sem:
        async with asyncio.timeout(30):
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            return response.json()
```

---

## 7. Correct Production Architecture

```
Incoming traffic
  ↓
Load Balancer (Kubernetes Service)
  ↓
[Per-Pod Layered Defense]
  ↓
  Local queue timeout (fail fast)
  ↓
  Local semaphore (machine capacity)
  ↓
  Local rate limiter (fairness / smoothing)
  ↓
  GLOBAL vendor rate limiter (Redis)
  ↓
Vendor API
```

### Responsibility Separation

| Layer | Scope | Purpose |
|-------|-------|---------|
| Queue timeout | Local | Admission control |
| Semaphore | Local | Pod capacity protection |
| Local rate limiter | Local | Burst smoothing |
| Global rate limiter | Global | Vendor contract enforcement |

---

## 8. How to Choose Values

### Semaphore Size

```
= maximum concurrent calls the pod can safely handle
```

Based on:
- CPU cores
- Memory available
- Socket/connection limits
- Vendor latency profile

Typical values:
- Small pods: 20-50
- Medium pods: 50-100
- Large pods: 100-200

**Never**:
- Unlimited
- Larger than your socket pool
- Based on vendor limits (that's global)

### Local Rate Limiter

```
≈ (semaphore / avg_vendor_latency) × safety_margin

safety_margin ≈ 0.7 - 0.9
```

Example:
```
semaphore = 50
avg_latency = 30s
safety = 0.8

local_rate ≈ (50 / 30) × 0.8 ≈ 1.33 req/sec ≈ 80 req/min
```

### Global Rate Limiter

```
= vendor contract (exactly)
```

**Never**:
- Per pod
- Divided by number of pods
- Based on local capacity

---

## 9. Kubernetes Autoscaling Considerations

### The Problem

With Horizontal Pod Autoscaler (HPA):
- Pods scale up → total cluster capacity increases
- Per-pod rate limiters remain the same
- **Vendor limits are still global**

```
Initial: 10 pods × 80 req/min = 800 req/min (local capacity)
Scaled: 20 pods × 80 req/min = 1,600 req/min (local capacity)

But vendor limit = 500 req/min (global) ❌
```

### The Solution

**Global rate limiter must be**:
- Shared across all pods
- Independent of pod count
- Enforced at the cluster level

**Never**:
- Divide vendor limit by pod count
- Reconfigure on scale events
- Rely on "coordination" between pods

---

## 10. Rolling Deployments

### The Problem

During rolling deployments:
- Old pods still running
- New pods starting
- **Total pod count temporarily increases**

```
Normal: 10 pods
During deploy: 10 old + 5 new = 15 pods temporarily
```

Per-pod local limits based on "expected cluster size" will be wrong.

### The Solution

Use a **global rate limiter** that is:
- Independent of deployment state
- Shared across old and new pods
- Enforced regardless of pod count

---

## 11. Load Balancer Behavior

### Non-Deterministic Distribution

Load balancers distribute traffic:
- Round-robin
- Least connections
- Random
- **NOT based on internal pod state**

This means:
- One pod might be overloaded
- Another pod might be idle
- LB doesn't know about semaphore pressure

### Handling Imbalance

1. **Readiness probes** (reactive)
   - Overloaded pod fails health check
   - LB stops routing to it

2. **Fast queue timeouts** (proactive)
   - Overloaded pod rejects quickly (504/429)
   - Client retries
   - LB routes retry to different pod

3. **Connection-based routing**
   - Use least-connections algorithm
   - Better distribution under uneven load

---

## 12. Summary: Local vs Global

| Concern | Scope | Mechanism |
|---------|-------|-----------|
| Pod capacity | Local | Semaphore |
| Pod admission | Local | Queue timeout |
| Burst smoothing | Local | Local rate limiter |
| Vendor protection | Global | Redis rate limiter |

### Key Principles

1. ❌ Per-pod rate limiting does **NOT** protect vendor APIs
2. ✅ Per-pod rate limiting provides **local fairness**
3. ✅ Semaphores enforce **machine capacity**
4. ✅ Global rate limiting enforces **vendor contracts**
5. ❗ Kubernetes autoscaling and deployments require global coordination

### Design Principle

> **We enforce vendor limits globally and protect each pod locally from overload.**

---

**Next**: [Part 5: Production Architecture](05_production_architecture.md) — complete stack and execution order.
