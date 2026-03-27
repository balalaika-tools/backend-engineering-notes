# Part 6: Advanced Resilience Patterns

> **Principle**: Some mechanisms must be shared across pods, others must remain local.

---

## 0. Pod Awareness: Mental Model

In a multi-pod environment:
- Each pod has **its own memory**
- Load balancer distributes traffic **non-deterministically**
- Local state ≠ global truth
- **Coordination requires shared state** (Redis)

### Rule of Thumb

> **Protecting a downstream dependency → shared state**
> **Protecting a pod's own health → local state**

| Concern | Scope | State | Why |
|---------|-------|-------|-----|
| Circuit breaker | Global | Redis | All pods must agree when to stop |
| Vendor rate limit | Global | Redis | Vendor sees all pods |
| Semaphore | Local | In-memory | Pod capacity is per-pod |
| Load shedding | Local | In-memory | Pod health is local |
| Adaptive retry | Local | In-memory | Based on local conditions |

---

## 1. Circuit Breakers (🔴 MUST Be Pod-Aware)

### What Problem They Solve

When a vendor is **systematically failing**, retries across multiple pods:
- Multiply traffic against a broken system
- Burn global rate limits
- Create cascading failures
- Prevent recovery

A **pod-aware circuit breaker** ensures **all pods agree** when to stop calling.

### Circuit Breaker States

| State | Behavior |
|-------|----------|
| **Closed** | Normal operation |
| **Open** | All pods fail fast (no vendor calls) |
| **Half-open** | Limited probe calls globally |

### State Transitions

```
Closed → Open: After N failures in window
Open → Half-open: After timeout
Half-open → Closed: After M successes
Half-open → Open: After any failure
```

### Where It Belongs

```
Queue timeout
  ↓
Circuit breaker (GLOBAL fail fast)
  ↓
Global vendor rate limiter
  ↓
Local rate limiter (optional)
  ↓
Semaphore
  ↓
Vendor call
```

**Critical**: Circuit breaker comes **before** rate limiting.

If breaker is open, don't waste rate limit tokens or semaphore slots.

### Why In-Memory Breakers Are WRONG

Without shared state:

```
Pod A → breaker OPEN
Pod B → breaker CLOSED
Pod C → breaker CLOSED
```

Result: Vendor still receives traffic from Pods B and C.

**Circuit breakers MUST be shared (Redis).**

### Redis-Backed Circuit Breaker

```python
import redis.asyncio as redis
import time


class CircuitBreakerOpen(Exception):
    pass


class PodAwareCircuitBreaker:
    """
    Redis-backed circuit breaker shared across all pods.
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        key: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout_seconds: int = 60,
    ):
        self.redis = redis_client
        self.key_prefix = f"breaker:{key}"
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout_seconds
    
    async def allow(self) -> None:
        """Raises CircuitBreakerOpen if breaker is open."""
        state = await self._get_state()
        
        if state == "open":
            opened_at = await self.redis.get(f"{self.key_prefix}:opened_at")
            if opened_at:
                elapsed = time.time() - float(opened_at)
                if elapsed > self.timeout:
                    await self._set_state("half_open")
                else:
                    raise CircuitBreakerOpen()
            else:
                raise CircuitBreakerOpen()
    
    async def record_success(self) -> None:
        state = await self._get_state()
        
        if state == "half_open":
            successes = await self.redis.incr(f"{self.key_prefix}:successes")
            if successes >= self.success_threshold:
                await self._set_state("closed")
                await self.redis.set(f"{self.key_prefix}:failures", 0)
        elif state == "closed":
            await self.redis.set(f"{self.key_prefix}:failures", 0)
    
    async def record_failure(self) -> None:
        state = await self._get_state()
        
        if state == "half_open":
            await self._set_state("open")
            await self.redis.set(f"{self.key_prefix}:opened_at", time.time())
        elif state == "closed":
            failures = await self.redis.incr(f"{self.key_prefix}:failures")
            await self.redis.expire(f"{self.key_prefix}:failures", 60)
            
            if failures >= self.failure_threshold:
                await self._set_state("open")
                await self.redis.set(f"{self.key_prefix}:opened_at", time.time())
    
    async def _get_state(self) -> str:
        state = await self.redis.get(f"{self.key_prefix}:state")
        return state.decode() if state else "closed"
    
    async def _set_state(self, state: str) -> None:
        await self.redis.set(f"{self.key_prefix}:state", state)


# Usage
async def call_llm_with_breaker(payload: dict):
    await breaker.allow()  # Check FIRST
    
    try:
        result = await call_llm(payload)
        await breaker.record_success()
        return result
    except Exception as e:
        await breaker.record_failure()
        raise
```

---

## 2. Priority Queues (🟡 Pod-Aware IF Backlog Exists)

### When Redis Queue Is REQUIRED

✅ Use Redis/broker when:
- Mixed workloads (realtime + batch)
- Different SLAs / user tiers
- High traffic with variable latency
- Long-running tasks

❌ Not needed when:
- All requests are equivalent
- No backlog (requests complete quickly)
- Low traffic

### Pod-Local Queue (Simple Case)

```python
import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class PriorityQueueItem:
    priority: int
    payload: Any = field(compare=False)
    future: asyncio.Future = field(compare=False)


priority_queue = asyncio.PriorityQueue()


async def submit_with_priority(payload: dict, priority: int):
    """Lower number = higher priority."""
    future = asyncio.Future()
    await priority_queue.put(PriorityQueueItem(priority, payload, future))
    return await future


async def worker():
    while True:
        item = await priority_queue.get()
        try:
            result = await call_llm(item.payload)
            item.future.set_result(result)
        except Exception as e:
            item.future.set_exception(e)
```

> ⚠️ This is **not pod-aware**. Each pod has its own queue.

### Redis Priority Queue (Multi-Pod)

```python
class RedisPriorityQueue:
    """Shared priority queue using Redis sorted set."""
    
    def __init__(self, redis_client: redis.Redis, key: str):
        self.redis = redis_client
        self.key = key
    
    async def push(self, payload: dict, priority: int) -> str:
        task_id = f"{time.time()}:{id(payload)}"
        await self.redis.zadd(
            self.key,
            {json.dumps({"id": task_id, "payload": payload}): priority}
        )
        return task_id
    
    async def pop(self, timeout: float = 1.0) -> Optional[dict]:
        result = await self.redis.bzpopmin(self.key, timeout=timeout)
        if result:
            key, member, score = result
            return json.loads(member)
        return None
```

---

## 3. Adaptive Retry (🟢 Pod-Local by Design)

### Why Local

Adaptive retry decisions depend on:
- Local latency
- Local queue pressure
- Local semaphore contention
- Global breaker state (already shared)

Each pod should adapt to **its own health**.

### Retry Budget Computation

```python
def compute_retry_budget(
    *,
    breaker_open: bool,
    queue_wait_seconds: float,
    recent_error_rate: float,
    semaphore_pressure: float,
) -> int:
    """
    Compute how many retries are safe right now.
    Returns 0-3.
    """
    
    # No retries if circuit breaker is open
    if breaker_open:
        return 0
    
    # No retries if queue is backing up
    if queue_wait_seconds > 2.0:
        return 0
    
    # No retries if semaphore is saturated
    if semaphore_pressure > 0.9:
        return 0
    
    # Fewer retries if error rate is high
    if recent_error_rate > 0.5:
        return 1
    
    if recent_error_rate > 0.2:
        return 2
    
    # Full retries if system is healthy
    return 3
```

### Usage

```python
async def call_with_adaptive_retry(payload: dict):
    max_retries = compute_retry_budget(
        breaker_open=await breaker.is_open(),
        queue_wait_seconds=metrics.get_average_queue_wait(),
        recent_error_rate=metrics.get_error_rate(),
        semaphore_pressure=get_semaphore_pressure(),
    )
    
    for attempt in range(max_retries + 1):
        try:
            return await call_llm_with_breaker(payload)
        except RetryableError:
            if attempt == max_retries:
                raise
            await asyncio.sleep(backoff(attempt))
```

---

## 4. Load Shedding (🟢 Mostly Pod-Local)

### What It Is

**Intentional rejection** to protect the system.

> "If THIS pod is unhealthy, it rejects early."

### Where Load Shedding Belongs

```
Client
  ↓
Load shedding (local reject)
  ↓
Queue timeout
  ↓
Circuit breaker (global)
  ↓
Rate limiter
```

**Earlier = cheaper.** Reject before doing any work.

### Queue-Length Shedding

```python
MAX_QUEUE_SIZE = 100


@app.post("/llm")
async def llm_endpoint(payload: dict):
    if priority_queue.qsize() > MAX_QUEUE_SIZE:
        raise HTTPException(429, "System overloaded - queue full")
    
    return await call_llm(payload)
```

### Latency-Based Shedding

```python
def should_shed_load() -> bool:
    p95 = get_p95_latency()
    return p95 > SLA_LATENCY * 0.9


@app.post("/llm")
async def llm_endpoint(payload: dict):
    if should_shed_load():
        raise HTTPException(503, "Service temporarily overloaded")
    
    return await call_llm(payload)
```

### Tier-Based Shedding

```python
def should_shed_for_tier(tier: str) -> bool:
    pressure = get_system_pressure()
    
    if pressure > 0.9:
        # Extreme pressure: only serve premium
        return tier != "premium"
    
    elif pressure > 0.7:
        # Moderate pressure: shed free tier
        return tier == "free"
    
    return False


@app.post("/llm")
async def llm_endpoint(payload: dict, tier: str = "free"):
    if should_shed_for_tier(tier):
        raise HTTPException(429, f"Service under pressure - {tier} tier limited")
    
    return await call_llm(payload)
```

---

## 5. Local Metrics Tracking

```python
from collections import deque
import time


class LocalMetrics:
    """Track local pod metrics for adaptive decisions."""
    
    def __init__(self, window_size: int = 100):
        self.recent_calls = deque(maxlen=window_size)
        self.queue_waits = deque(maxlen=50)
    
    def record_call(self, success: bool, duration: float):
        self.recent_calls.append({
            "success": success,
            "duration": duration,
            "timestamp": time.time(),
        })
    
    def record_queue_wait(self, wait_seconds: float):
        self.queue_waits.append(wait_seconds)
    
    def get_error_rate(self) -> float:
        if not self.recent_calls:
            return 0.0
        errors = sum(1 for call in self.recent_calls if not call["success"])
        return errors / len(self.recent_calls)
    
    def get_average_queue_wait(self) -> float:
        if not self.queue_waits:
            return 0.0
        return sum(self.queue_waits) / len(self.queue_waits)


# Per-pod instance
local_metrics = LocalMetrics()
```

---

## 6. Complete End-to-End Flow

```
Request arrives
  ↓
1. Load shedding (local, fast reject)
  ↓
2. User rate limit check (global, Redis)
  ↓
3. Queue timeout (local, wait limit)
  ↓
4. Circuit breaker check (global, Redis)
  ↓
5. Global vendor rate limiter (Redis)
  ↓
6. Local rate limiter (optional, burst smoothing)
  ↓
7. Semaphore (local, capacity)
  ↓
8. Call timeout
  ↓
9. Vendor call (HTTP client with timeouts)
  ↓
10. Adaptive retry (local decision)
```

---

## 7. Combined Implementation

```python
async def complete_llm_call(
    payload: dict,
    user_id: str,
    tier: str,
) -> dict:
    """Complete production LLM call with all safeguards."""
    
    # 1. Load shedding (local)
    if should_shed_for_tier(tier):
        raise HTTPException(429, "Service under pressure")
    
    # 2. User rate limit (global)
    await check_user_rate_limit(user_id, tier)
    
    try:
        # 3. Queue timeout
        async with asyncio.timeout(5):
            
            # 4. Circuit breaker (global)
            await breaker.allow()
            
            # 5. Global vendor rate limiter
            await vendor_limiter.wait_and_acquire(timeout=3.0)
            
            # 6. Local rate limiter
            async with llm_rate_local:
                
                # 7. Semaphore
                async with llm_sem:
                    
                    # 8. Call timeout
                    async with asyncio.timeout(30):
                        response = await client.post(url, json=payload)
                        response.raise_for_status()
                        result = response.json()
            
            # Success
            await breaker.record_success()
            local_metrics.record_call(success=True, duration=...)
            return result
    
    except CircuitBreakerOpen:
        raise HTTPException(503, "Service temporarily unavailable")
    
    except VendorRateLimitExceeded:
        raise HTTPException(429, "Vendor rate limit exceeded")
    
    except asyncio.TimeoutError:
        await breaker.record_failure()
        local_metrics.record_call(success=False, duration=...)
        raise HTTPException(504, "Request timeout")
    
    except httpx.HTTPStatusError as e:
        await breaker.record_failure()
        local_metrics.record_call(success=False, duration=...)
        raise HTTPException(502, f"Vendor error: {e.response.status_code}")
```

---

## 8. When to Add These Patterns

### Start With (Baseline)

- Semaphore (local capacity)
- Call timeout
- Queue timeout
- Basic retry logic
- User rate limiting (Redis)
- Global vendor rate limiter (Redis)

### Add When Needed

**Circuit breaker** (Redis):
- Frequent vendor outages
- Cascading failures observed
- Need coordinated failure across pods

**Priority queues** (Redis):
- Mixed workloads (realtime + batch)
- Different SLAs
- Long queues

**Adaptive retry**:
- High error rates
- Variable latency
- Need to reduce retry storms

**Load shedding**:
- Traffic spikes
- Tier-based service
- Need graceful degradation

---

## 9. Pod-Awareness Rules (Non-Negotiable)

1. ✅ Circuit breakers **must be shared** (Redis)
2. ✅ Vendor rate limiters **must be shared** (Redis)
3. ✅ User rate limiters **must be shared** (Redis)
4. ⚠️ Priority queues are shared **only if backlog exists**
5. ❌ Adaptive retries are always **local**
6. ❌ Load shedding is **early and local**
7. ❌ Semaphores are always **local**
8. ❗ Never sleep while holding semaphores
9. ❗ Never retry during overload
10. ❗ Global limits protect vendors, local limits protect pods

---

## Summary: What Lives Where

| Mechanism | Scope | Storage | Purpose |
|-----------|-------|---------|---------|
| Semaphore | Local | In-memory | Pod capacity |
| Local rate limiter | Local | In-memory | Burst smoothing |
| Queue timeout | Local | In-memory | Admission control |
| Load shedding | Local | In-memory | Early rejection |
| Adaptive retry | Local | In-memory | Conditional retry |
| Circuit breaker | Global | Redis | Coordinated failure |
| Vendor rate limiter | Global | Redis | Vendor contract |
| User rate limiter | Global | Redis | Fairness |
| Priority queue | Global | Redis | Global ordering |

---

## Final Thought

In distributed systems:

> **Correctness is not local. Failure must be coordinated.**

A pod-aware system ensures **all pods fail together, on purpose, and fast**.

This prevents:
- Cascading failures
- Retry storms
- Vendor limit violations
- Resource exhaustion

**The right architecture makes failure safe, predictable, and recoverable.**

---

---

**Next**: [Part 7: Streaming Patterns](07_streaming_patterns.md) — how streaming changes timeout semantics, concurrency, and cancellation.
