# Part 3: Call Patterns and Retry Logic

> **Principle**: Retries must happen outside the semaphore. Never sleep while holding resources.

---

## 1. The Gold-Standard Call Pattern

This is the **recommended baseline** for LLM or external API calls:

```python
import asyncio
import httpx
import random
from aiolimiter import AsyncLimiter


class RetryableError(Exception):
    """Transient errors that may succeed on retry"""
    pass


class FinalFailure(Exception):
    """Permanent failure after all retries exhausted"""
    pass


# === GLOBAL PRIMITIVES (must be created once at app startup) ===
llm_sem = asyncio.Semaphore(50)           # Local concurrency control
llm_rate = AsyncLimiter(60, 60)           # Local rate limiting (60/min)
client: httpx.AsyncClient = None          # Initialized in startup event


def backoff(attempt: int) -> float:
    """Exponential backoff with jitter"""
    base = min(2 ** attempt, 32)
    return base + random.uniform(0, 1)


async def call_llm(payload: dict):
    """
    Gold standard LLM call with:
    - Queue timeout (admission control)
    - Rate limiting (throughput control)
    - Semaphore (concurrency control)
    - Call timeout (latency control)
    - Retry logic with proper exception handling
    """
    for attempt in range(3):
        try:
            # Queue timeout: do not wait forever to START
            async with asyncio.timeout(5):
                async with llm_rate:        # throughput control
                    async with llm_sem:     # concurrency control
                        
                        # Call timeout: do not let vendor hang
                        async with asyncio.timeout(30):
                            response = await client.post(
                                "https://vendor/api",
                                json=payload,
                            )
                            response.raise_for_status()
                            return response.json()
        
        except httpx.TimeoutException:
            # Vendor / network slowness → retry
            if attempt < 2:
                await asyncio.sleep(backoff(attempt))
                continue
            raise FinalFailure("Vendor timeout after retries")
        
        except httpx.HTTPStatusError as e:
            # 5xx → retry, 4xx → don't retry
            if 500 <= e.response.status_code < 600:
                if attempt < 2:
                    await asyncio.sleep(backoff(attempt))
                    continue
            raise FinalFailure(f"HTTP error: {e.response.status_code}")
        
        except asyncio.TimeoutError:
            # Could be queue timeout OR call timeout
            # First attempt → might be call timeout, try once more
            if attempt == 0:
                await asyncio.sleep(backoff(attempt))
                continue
            # Subsequent timeout → likely system overload
            raise FinalFailure("Timeout after retries")
    
    raise FinalFailure("All retries exhausted")
```

---

## 2. Why This Pattern Works

### Layer Order

```
1. for attempt in range(3):     ← retry loop OUTSIDE everything
2.   async with timeout(5):     ← queue timeout
3.     async with rate:         ← throughput control
4.       async with sem:        ← concurrency control
5.         async with timeout:  ← call timeout
6.           await client.post  ← actual call
```

**Critical**: Retry loop is **outermost**. Semaphore is released before sleeping.

### What Each Layer Does

| Layer | Purpose | Failure = |
|-------|---------|-----------|
| Retry loop | Transient failure recovery | Exhaust retries |
| Queue timeout | Admission control | System overloaded |
| Rate limiter | Vendor quota compliance | Throughput exceeded |
| Semaphore | Concurrency control | Capacity reached |
| Call timeout | Per-attempt budget | Vendor slow |

---

## 3. Retry Rules (Critical)

### ✅ Retry These

- Transient network errors (`httpx.ConnectError`)
- 5xx responses (server errors)
- Vendor slowness (`httpx.TimeoutException`)
- Call timeout (`asyncio.TimeoutError` after first attempt)

### ❌ Do NOT Retry These

- Queue timeouts (system is overloaded)
- 4xx responses (client errors)
- Authentication errors (401, 403)
- Validation errors (400, 422)
- Rate limit responses (429) — use backoff instead

**Rule of thumb**:

> If the system is already overloaded, retries make it worse.

---

## 4. Semaphore + Retry: Non-Negotiable Rule

> **Retries must happen OUTSIDE the semaphore scope.**

### ✅ Correct

```python
for attempt in range(3):
    async with llm_sem:
        result = await call_vendor()
    
    if needs_retry:
        await asyncio.sleep(backoff(attempt))
        continue
    
    return result
```

The semaphore is released **before** sleeping.

### ❌ Incorrect

```python
async with llm_sem:
    for attempt in range(3):
        result = await call_vendor()
        if needs_retry:
            await asyncio.sleep(backoff(attempt))  # SLEEPING WHILE HOLDING SEM!
```

### Why This Matters

If you sleep while holding a semaphore:
- Throughput **collapses**
- Other requests are blocked unnecessarily
- Effective concurrency = `sem_size / avg_retries`
- Deadlocks appear under load

---

## 5. Rate Limiter Behavior

`AsyncLimiter` (and most rate limiters):
- **Waits** (does not reject immediately)
- Does NOT return 429 to client
- Queues callers fairly

```python
from aiolimiter import AsyncLimiter

llm_rate = AsyncLimiter(60, 60)  # 60 calls per minute

async with llm_rate:  # waits until token available
    await client.post(...)
```

This means:
- FastAPI requests may wait
- Workers are not blocked (async)
- Latency increases instead of hard failing

**If you want hard rejection**, implement it:

```python
async def call_with_hard_reject():
    # Try to acquire immediately
    try:
        async with asyncio.timeout(0.1):  # very short
            async with llm_rate:
                pass  # acquired
    except asyncio.TimeoutError:
        raise HTTPException(429, "Rate limit - try later")
    
    # Now actually make the call
    async with llm_rate:
        return await client.post(...)
```

---

## 6. Backoff Strategies

### Exponential Backoff with Jitter

```python
import random

def backoff(attempt: int) -> float:
    """
    Exponential backoff with jitter.
    
    attempt 0: 1-2 sec
    attempt 1: 2-3 sec
    attempt 2: 4-5 sec
    ...
    capped at 32-33 sec
    """
    base = min(2 ** attempt, 32)
    jitter = random.uniform(0, 1)
    return base + jitter
```

**Why jitter?** Prevents thundering herd when multiple requests retry simultaneously.

### Decorrelated Jitter (Better for High Contention)

```python
def decorrelated_backoff(attempt: int, prev_backoff: float) -> float:
    """
    Decorrelated jitter for better spread.
    """
    return min(32, random.uniform(1, prev_backoff * 3))
```

### Conservative (429 Responses)

```python
def respect_retry_after(response: httpx.Response) -> float:
    """Use vendor's Retry-After header if present."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        return float(retry_after)
    return backoff(attempt)
```

---

## 7. Exception Classification

```python
async def classify_and_handle(e: Exception, attempt: int):
    """
    Classify exception and decide whether to retry.
    """
    max_retries = 3
    
    # Transport errors — usually retry
    if isinstance(e, httpx.TimeoutException):
        if attempt < max_retries - 1:
            return "retry"
        return "fail"
    
    if isinstance(e, httpx.ConnectError):
        if attempt < max_retries - 1:
            return "retry"
        return "fail"
    
    # HTTP errors — depends on status code
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        
        # 5xx — retry
        if 500 <= code < 600:
            if attempt < max_retries - 1:
                return "retry"
            return "fail"
        
        # 429 — retry with backoff
        if code == 429:
            if attempt < max_retries - 1:
                return "retry_with_longer_backoff"
            return "fail"
        
        # 4xx — don't retry
        return "fail_no_retry"
    
    # Application timeout — context dependent
    if isinstance(e, asyncio.TimeoutError):
        # Only retry once for timeouts
        if attempt == 0:
            return "retry"
        return "fail"
    
    # Unknown — don't retry
    return "fail_no_retry"
```

---

## 8. Complete Production Call Pattern

```python
import asyncio
import httpx
from typing import Optional
import structlog

logger = structlog.get_logger()


async def call_llm_production(
    payload: dict,
    *,
    request_id: Optional[str] = None,
) -> dict:
    """
    Production-grade LLM call with:
    - Structured logging
    - Proper exception handling
    - Retry logic
    - All timeout layers
    """
    
    log = logger.bind(
        request_id=request_id,
        payload_keys=list(payload.keys()),
    )
    
    for attempt in range(3):
        log = log.bind(attempt=attempt)
        
        try:
            # Queue timeout: admission control
            async with asyncio.timeout(5):
                log.debug("acquiring_rate_limit")
                
                async with llm_rate:
                    log.debug("acquiring_semaphore")
                    
                    async with llm_sem:
                        log.info("calling_vendor")
                        
                        # Call timeout: per-attempt latency control
                        async with asyncio.timeout(30):
                            response = await client.post(
                                "https://vendor/api",
                                json=payload,
                                headers={"X-Request-ID": request_id} if request_id else {},
                            )
                            response.raise_for_status()
                            
                            result = response.json()
                            
                            log.info(
                                "vendor_success",
                                status_code=response.status_code,
                            )
                            
                            return result
        
        except httpx.TimeoutException as e:
            log.warning("vendor_timeout", error=str(e))
            
            if attempt < 2:
                wait = backoff(attempt)
                log.debug("retrying", wait_seconds=wait)
                await asyncio.sleep(wait)
                continue
            
            raise FinalFailure(f"Vendor timeout after {attempt + 1} attempts")
        
        except httpx.HTTPStatusError as e:
            log.warning("vendor_http_error", status_code=e.response.status_code)
            
            if 500 <= e.response.status_code < 600:
                if attempt < 2:
                    wait = backoff(attempt)
                    await asyncio.sleep(wait)
                    continue
            
            raise FinalFailure(f"Vendor error: {e.response.status_code}")
        
        except asyncio.TimeoutError:
            log.warning("application_timeout")
            
            if attempt == 0:
                wait = backoff(attempt)
                await asyncio.sleep(wait)
                continue
            
            raise FinalFailure(f"Timeout after {attempt + 1} attempts")
    
    raise FinalFailure("All retries exhausted")
```

---

## 9. SDK Retries: Disable or Account For

Many vendor SDKs (OpenAI, Anthropic, etc.) have **built-in retries**.

### The Problem

```
Your retry × SDK retry = retry multiplication

3 × 3 = 9 actual attempts
```

Effects:
- Rate limits blown
- Latency multiplied  
- Cascading failures

### Solution 1: Disable SDK Retries (Recommended)

```python
# OpenAI
client = openai.AsyncClient(
    max_retries=0,  # YOU control retries
    timeout=30.0,
)

# Anthropic
client = anthropic.AsyncAnthropic(
    max_retries=0,
)
```

### Solution 2: Account for SDK Retries

If you can't disable them:

```python
# SDK will retry 3 times internally
# So your outer retry should be 1
for attempt in range(1):  # Only ONE outer retry
    try:
        result = await sdk_call()
        break
    except Exception:
        if attempt == 0:
            await asyncio.sleep(backoff(attempt))
```

**Rule**: One retry layer. Yours. Control it explicitly.

---

## 10. FastAPI Integration

```python
from fastapi import FastAPI, HTTPException
import asyncio

app = FastAPI()


@app.post("/llm")
async def llm_endpoint(payload: dict):
    """
    Complete LLM endpoint with SLA boundary protection.
    """
    try:
        # SLA boundary: absolute maximum latency
        async with asyncio.timeout(60):
            return await call_llm_production(payload)
    
    except asyncio.TimeoutError:
        raise HTTPException(504, "Request timeout")
    
    except FinalFailure as e:
        raise HTTPException(502, f"Upstream failure: {str(e)}")
    
    except Exception as e:
        logger.exception("Unexpected error")
        raise HTTPException(500, "Internal server error")
```

### What Clients See

| Situation | HTTP Status | Client Experience |
|-----------|-------------|-------------------|
| Queue overload | 504 / 429 | Fast fail |
| Vendor slow | 200 | Retry succeeded (transparent) |
| Vendor down | 502 | Controlled failure |
| System healthy | 200 | Normal latency |
| SLA timeout | 504 | Gateway timeout |

---

## 11. Common Mistakes

### ❌ Semaphore inside retry

```python
async with llm_sem:
    for attempt in range(3):
        result = await call()
        await asyncio.sleep(backoff(attempt))  # WRONG
```

### ❌ No queue timeout

```python
async with llm_rate:
    async with llm_sem:
        # Request might wait forever here
        await call()
```

### ❌ Catching only `asyncio.TimeoutError`

```python
try:
    await client.post(...)
except asyncio.TimeoutError:  # MISSES httpx timeouts!
    ...
```

### ❌ Retrying queue timeouts

```python
except asyncio.TimeoutError:
    await asyncio.sleep(backoff(attempt))
    continue  # System is overloaded, this makes it worse
```

### ❌ Per-request primitives

```python
@app.post("/llm")
async def endpoint():
    sem = asyncio.Semaphore(50)  # USELESS - new every request
    async with sem:
        ...
```

---

## 12. Summary: Retry Decision Tree

```
Exception occurred
    │
    ├─ httpx.TimeoutException?
    │   └─ Yes → Retry (if attempts remain)
    │
    ├─ httpx.ConnectError?
    │   └─ Yes → Retry (if attempts remain)
    │
    ├─ httpx.HTTPStatusError?
    │   ├─ 5xx → Retry (if attempts remain)
    │   ├─ 429 → Retry with Retry-After header
    │   └─ 4xx → Do NOT retry
    │
    ├─ asyncio.TimeoutError?
    │   ├─ First attempt → Retry once
    │   └─ Subsequent → Do NOT retry (overloaded)
    │
    └─ Other → Do NOT retry
```

---

## Key Principles

1. **Retry loop outside semaphore** — never sleep while holding resources
2. **Queue timeout is not retryable** — overload should fail fast
3. **httpx exceptions ≠ asyncio exceptions** — handle both
4. **SDK retries multiply** — disable or account for them
5. **Backoff with jitter** — prevent thundering herd

---

**Next**: [Part 4: Kubernetes](04_kubernetes.md) — multi-pod concerns and distributed systems.
