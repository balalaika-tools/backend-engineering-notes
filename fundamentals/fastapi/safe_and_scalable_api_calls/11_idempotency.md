# Idempotency Keys

> Retries without idempotency double-charge customers, send duplicate emails, and ship two of the same order. An idempotency key turns "did the first attempt succeed?" from an unanswerable question into a server-side lookup. This guide explains why retries *require* idempotency, how the key flows through the stack, and how to implement the server side safely.

---

## 1. The Problem

[Part 03 — Call Patterns](03_call_patterns.md) shows retry loops with exponential backoff, circuit breakers, the works. Every one of those loops has a hidden premise: **the upstream operation is safe to repeat**. For `GET /invoice/123` it is. For `POST /payments` with `{ amount: 100, card: "..." }` it is emphatically not.

The ambiguous case is what kills you. Your POST sent; the upstream server received it, charged the card, started writing its response — and then *your side* timed out. You have no idea whether the charge went through. Retry and you might double-charge. Give up and you might refuse a successful charge on the next page load.

Idempotency keys resolve this. You generate a unique ID per **logical operation** and send it with every attempt. The server remembers the first attempt's result and replays it on retries. Retry safely, no matter the failure mode.

---

## 2. What "Idempotent" Actually Means

A request is **idempotent** if applying it once and applying it N times produces the same effect.

| HTTP method | Idempotent by spec? | Reality |
|-------------|---------------------|---------|
| `GET`, `HEAD`, `OPTIONS` | Yes | Safe to retry freely |
| `PUT` | Yes | "Set resource to this state" — repeat is a no-op |
| `DELETE` | Yes | "Make this gone" — repeat is a no-op |
| `POST` | **No** | Creates / charges / mutates — repeat duplicates |
| `PATCH` | Sometimes | Depends on the operation (`$inc` is not idempotent; `$set` is) |

The spec says POST is not idempotent. That is not a bug — `POST /orders` creating two orders for two identical requests is the correct reading of HTTP semantics. **Idempotency keys add idempotency to POST** explicitly, on a per-operation basis.

---

## 3. The Key Itself

- **Client-generated**, opaque to the server. Usually a UUID4 or similar high-entropy string.
- Represents **one logical operation**. A user clicking "Pay" once → one key. If the user clicks again intentionally (new intent), → new key.
- Sent in a request header, conventionally `Idempotency-Key`.
- Scope: usually per-endpoint or per-resource. Stripe scopes keys per-account+endpoint; most internal APIs scope per-endpoint+tenant.
- TTL: long enough to cover all retries of a single operation. 24 hours is common; Stripe is 24h.

```
POST /payments HTTP/1.1
Idempotency-Key: 2f1a3e40-1234-4abc-9def-0123456789ab
Content-Type: application/json

{"amount": 100, "card": "tok_abc"}
```

---

## 4. Server Responsibility — The State Machine

When a request arrives with an idempotency key, the server runs this decision tree:

```
┌─────────────────────────────────────┐
│  Key not in store                   │
│  → reserve (write status=in_flight) │
│  → execute operation                │
│  → store result                     │
│  → return response                  │
├─────────────────────────────────────┤
│  Key exists, status=completed       │
│  → return stored response verbatim  │
│  → do NOT re-execute                │
├─────────────────────────────────────┤
│  Key exists, status=in_flight       │
│  → 409 Conflict (or wait + poll)    │
│  → client should not retry yet      │
├─────────────────────────────────────┤
│  Key exists, same endpoint + body   │
│  → treat as replay (see above)      │
├─────────────────────────────────────┤
│  Key exists, DIFFERENT body         │
│  → 422 Unprocessable Entity         │
│  → "key reused with different args" │
└─────────────────────────────────────┘
```

The "different body" check is what stops a client accidentally reusing a key across operations. Hash the request body and store the hash alongside the result; on replay compare hashes.

### Atomicity

The **reserve** step must be atomic. If you `SELECT` then `INSERT`, two concurrent retries both see "not in store" and both execute. Use one of:

- `INSERT ... ON CONFLICT DO NOTHING RETURNING ...` (Postgres) — the row you get back tells you if you won the race.
- `SET NX` (Redis) — returns `OK` if this is the first writer, nil otherwise.
- A unique constraint on `(idempotency_key, endpoint, tenant_id)` — second writer gets a `UniqueViolation`, which you catch and convert to a replay.

The **store result** step must happen in the same transaction as the effect it represents, or the effect must be driven off the stored result. Two patterns:

1. **Same transaction.** In Postgres: insert the idempotency record and the payment record in one transaction. Crash between attempt and commit → neither row exists → retry re-executes cleanly.
2. **Outbox / saga.** If the effect crosses systems (charge a card via Stripe, then write to DB), reserve the idempotency key first, make the external call with the key forwarded, and on return update the idempotency row with the response. Stripe (or the upstream) is responsible for dedup on its side using the forwarded key.

---

## 5. FastAPI Implementation (Postgres-backed)

```python
import hashlib
import json
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import Column, String, Integer, JSON, DateTime, UniqueConstraint, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import declarative_base


Base = declarative_base()


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("key", "endpoint", "tenant_id"),
    )
    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=False)
    endpoint = Column(String, nullable=False)
    tenant_id = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    status = Column(String, nullable=False)  # "in_flight" | "completed"
    response_status = Column(Integer, nullable=True)
    response_body = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)


def _hash_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


async def handle_idempotent(
    db: AsyncSession,
    *,
    key: str,
    endpoint: str,
    tenant_id: str,
    request_body: bytes,
    execute,  # async callable → returns (status_code, response_dict)
) -> tuple[int, dict]:
    body_hash = _hash_body(request_body)

    # 1. Atomic reserve: INSERT ... ON CONFLICT DO NOTHING
    stmt = (
        pg_insert(IdempotencyRecord)
        .values(
            key=key,
            endpoint=endpoint,
            tenant_id=tenant_id,
            request_hash=body_hash,
            status="in_flight",
        )
        .on_conflict_do_nothing(index_elements=["key", "endpoint", "tenant_id"])
        .returning(IdempotencyRecord.id)
    )
    result = await db.execute(stmt)
    inserted_id = result.scalar_one_or_none()

    if inserted_id is None:
        # Conflict — someone else owns this key
        existing = await db.scalar(
            select(IdempotencyRecord)
            .where(IdempotencyRecord.key == key)
            .where(IdempotencyRecord.endpoint == endpoint)
            .where(IdempotencyRecord.tenant_id == tenant_id)
        )
        if existing.request_hash != body_hash:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Idempotency-Key reused with different request body",
            )
        if existing.status == "in_flight":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Request with this Idempotency-Key is still in progress",
            )
        # status == "completed" → replay
        return existing.response_status, existing.response_body

    # 2. We won the race — execute the operation
    await db.commit()  # commit the in_flight reservation first
    try:
        status_code, body = await execute()
    except Exception:
        # Leave the row as in_flight; a retry after TTL will re-execute.
        # Or delete it here if you prefer retries to be immediate.
        raise

    # 3. Persist the result
    await db.execute(
        update(IdempotencyRecord)
        .where(IdempotencyRecord.id == inserted_id)
        .values(
            status="completed",
            response_status=status_code,
            response_body=body,
            completed_at=func.now(),
        )
    )
    await db.commit()
    return status_code, body
```

Endpoint usage:

```python
@app.post("/payments")
async def create_payment(
    request: Request,
    db: AsyncSession = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
):
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header required")

    body = await request.body()

    async def execute():
        payload = json.loads(body)
        charge = await stripe_charge(payload, idempotency_key=idempotency_key)
        return 201, {"id": charge.id, "amount": charge.amount}

    status_code, response = await handle_idempotent(
        db,
        key=idempotency_key,
        endpoint="POST /payments",
        tenant_id=current_user.tenant_id,
        request_body=body,
        execute=execute,
    )
    return JSONResponse(status_code=status_code, content=response)
```

Key detail: the idempotency key is **forwarded** to Stripe, so even if your record-keeping fails, Stripe refuses to double-charge.

---

## 6. Redis-Backed Variant (Short TTL, Higher Throughput)

Postgres is the safe default. Redis is appropriate when:

- You do not need the result beyond a few minutes (e.g. retry protection only, not long-term replay).
- You already have a Redis hot path and do not want another DB round-trip.
- You accept that a Redis failover can lose in-flight reservations (usually fine; clients retry).

```python
import redis.asyncio as redis

r: redis.Redis = ...

LOCK_TTL = 120  # seconds — long enough for any single operation
RESULT_TTL = 86400  # 24 hours

async def reserve(key: str, endpoint: str, body_hash: str) -> str:
    full_key = f"idem:{endpoint}:{key}"
    # SET NX returns True only if key did not exist
    ok = await r.set(full_key, f"in_flight:{body_hash}", nx=True, ex=LOCK_TTL)
    if ok:
        return "reserved"
    existing = await r.get(full_key)
    if existing.startswith("completed:"):
        return "replay"
    return "in_flight"
```

Store the completed response at the same key with `SET ... EX RESULT_TTL` on success.

---

## 7. Client Side — Retrying Correctly

**Generate the key once per logical operation. Reuse it across all retries.**

```python
import uuid
from tenacity import retry, stop_after_attempt, wait_exponential

async def pay(amount: int, card: str):
    key = str(uuid.uuid4())  # ← generated ONCE, before the retry loop

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
    )
    async def _attempt():
        r = await client.post(
            "https://api.example.com/payments",
            json={"amount": amount, "card": card},
            headers={"Idempotency-Key": key},
        )
        r.raise_for_status()
        return r.json()

    return await _attempt()
```

Anti-pattern: generating the key inside the `_attempt` function. Every retry gets a fresh key, and the server has no way to dedup.

### Which errors are safe to retry?

| Response | Retry? | Why |
|----------|--------|-----|
| Network error / timeout | Yes | Unknown whether request was processed |
| 5xx (except 501) | Yes | Server-side transient |
| 429 Too Many Requests | Yes, with `Retry-After` | Rate limited |
| 4xx (except 409, 425, 429) | No | Client error — retrying won't help |
| 409 Conflict | Maybe, after short delay | Server says "in flight" — wait and poll |

With an idempotency key, the retry costs nothing (the server dedups on replay), so err on the side of retrying for ambiguous failures.

---

## 8. Testing Idempotency

1. **Happy path.** Send request, assert 201. Send same request + same key, assert identical 201 (not a new record created).
2. **Different body, same key.** Assert 422.
3. **Concurrent retries.** Fire two requests with the same key in parallel; assert one gets the response, the other gets 409 or the replay. Assert exactly one side effect occurred in the system under test.
4. **Crash in the middle.** Inject a failure between "reserve" and "execute"; retry after TTL; assert exactly one effect.
5. **TTL expiry.** Advance the clock past TTL; retry with the same key; assert it re-executes (this is by design — TTL bounds the replay window).

---

## 9. Common Mistakes

| Mistake | Consequence |
|---------|-------------|
| Generating the key inside the retry loop | Every attempt gets a new key; server cannot dedup |
| No body hash check | Caller reuses a key for a different operation; server returns wrong response |
| Non-atomic reserve (SELECT then INSERT) | Race condition; two concurrent retries both execute |
| Storing only the response, not the status code | Replays come back as 200 OK regardless of original status |
| Not forwarding the key to upstream services | Your service dedupes but the upstream still double-charges |
| TTL shorter than the retry window | Late retries re-execute as if brand new |
| Using the request ID as the idempotency key | Every retry is a new request ID — no dedup |

---

## 10. Where This Fits in the Stack

| Layer | Role |
|-------|------|
| Client | Generate key once per operation, send on every retry |
| Load balancer / gateway | Pass `Idempotency-Key` through untouched |
| Your service | Reserve → execute → store result; replay on repeat |
| Upstream (Stripe, etc.) | Receives forwarded key; dedupes at *its* boundary |
| Storage (Postgres/Redis) | Holds the idempotency table with unique constraint |

Without idempotency keys, everything in [Part 03 — Call Patterns](03_call_patterns.md) is a double-billing generator for non-GET endpoints. With them, aggressive retries are safe — the cost of retrying a succeeded request is one extra database lookup.

---

## See also

- [03_call_patterns.md](03_call_patterns.md) — retry loops that this guide makes safe.
- [10_llm_token_economics.md](10_llm_token_economics.md) — Reserve → Retry → Reconcile has the same shape as reserve→execute→store.
- [../07_error_handling.md](../07_error_handling.md) — mapping exceptions to HTTP status codes.
