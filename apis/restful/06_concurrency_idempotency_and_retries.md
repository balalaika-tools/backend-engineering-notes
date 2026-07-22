# Concurrency, Idempotency, and Retries

> **Who this is for**: API designers handling concurrent writers, network ambiguity, and safe client recovery. Assumes [HTTP Semantics](02_http_semantics.md).

---

## 1. Distributed Outcomes Are Uncertain

If a response is lost after commit, the client cannot infer the server outcome from its timeout.

```text
client                    API                    database
  │ POST payment           │                        │
  │───────────────────────>│ INSERT + COMMIT ─────>│
  │                        │<───────────────────────│
  │<──── response lost ────X                        │
  │ timeout                │                        │
```

There are only three honest client states:

- Known success
- Known failure
- Unknown outcome

The contract needs a way to turn unknown into known: an idempotency key, client-selected resource ID, operation-status resource, or reconciliation query.

---

## 2. Concurrent Writes and Lost Updates

Two clients read version 7, modify different fields, then both write. Without a precondition, the last writer silently discards the first change.

```text
client A GET v7 ── PATCH name ──> v8
client B GET v7 ── PATCH email ─> v9, overwriting A if full stale state was sent
```

Expose a validator such as an `ETag`:

```http
GET /profiles/usr_123

HTTP/1.1 200 OK
ETag: "profile-7"
Content-Type: application/json

{"id":"usr_123","display_name":"Ada","email":"ada@example.com"}
```

Require it on the write:

```http
PATCH /profiles/usr_123
If-Match: "profile-7"
Content-Type: application/merge-patch+json

{"display_name":"Ada Lovelace"}
```

Perform the comparison and update atomically:

```sql
UPDATE profiles
SET display_name = :display_name,
    version = version + 1
WHERE tenant_id = :tenant_id
  AND id = :profile_id
  AND version = :expected_version;
```

If zero rows change, return `412 Precondition Failed`. Use `428 Precondition Required` when a protected operation omits the required precondition.

Do not calculate a strong `ETag` from unstable serialization or include volatile fields unless those fields are intended to participate in the write conflict.

---

## 3. Idempotent Methods Are Not Enough

HTTP defines `PUT` and `DELETE` as idempotent by intended semantics, but implementation side effects can violate consumer expectations:

```text
PUT /subscriptions/sub_123 {"status":"active"}
```

The state assignment is idempotent. Sending a welcome email every time the request arrives is not. Derive side effects from durable state transitions and deduplicate them.

`PATCH` can be idempotent or not:

```json
{"status":"shipped"}
```

This is a repeatable state assignment. By contrast:

```json
{"operation":"increment"}
```

Each repetition performs another increment.

Document operation semantics; method name alone cannot repair application logic.

---

## 4. Idempotency Keys for `POST`

For payments, orders, job starts, and expensive generation, accept a client-generated key scoped to the caller and operation.

```http
POST /payments
Idempotency-Key: 018f4f8a-4a8e-7d7c-b8e4-a7926c5e9912
Content-Type: application/json

{"invoice_id":"inv_123","amount_minor":4200,"currency":"EUR"}
```

Persist a state machine:

| Scope + key | Request fingerprint | State | Stored outcome |
|-------------|---------------------|-------|----------------|
| tenant 7 + key A | SHA-256 of canonical operation input | in progress | — |
| tenant 7 + key B | SHA-256 ... | completed | status, selected headers, body |

Processing rules:

1. Atomically claim a new key before performing the side effect.
2. Same key and same fingerprint while in progress returns a documented conflict/in-progress response.
3. Same key and same fingerprint after completion replays the stored outcome.
4. Same key with a different fingerprint is rejected.
5. Key retention exceeds the maximum realistic retry/reconciliation window.

```text
new ──claim──> in_progress ──commit outcome──> completed
 │                 │                              │
 └─same key────────┴──────── replay/wait ─────────┘
```

Store enough of the response to preserve the contract, but do not retain secrets unnecessarily. A database unique constraint on `(principal_scope, operation, key)` is the final race guard.

See the repository's detailed [Idempotency Keys](../../fundamentals/fastapi/safe_and_scalable_api_calls/11_idempotency.md) implementations.

---

## 5. Retry Classification

Retry only when all conditions hold:

- Failure is plausibly transient.
- Operation is safe, idempotent, or protected by deduplication.
- Remaining end-to-end deadline can accommodate another attempt.
- Attempt and global retry budgets permit it.
- Backoff will not amplify overload.

| Outcome | Default client action |
|---------|-----------------------|
| DNS/connect/transient reset before sending | Retry safe/idempotent operation within budget |
| `408`, `429`, selected `502`/`503`/`504` | Consider retry; honor server guidance |
| `400`, `401`, `403`, `404`, `405`, `415`, `422` | Correct request/credentials; do not blind retry |
| `409`, `412` | Re-read/reconcile or restart higher-level transaction |
| Read timeout after unsafe operation may have committed | Query by idempotency key/operation ID; do not send an unprotected duplicate |

A `500` may be a deterministic application bug. Repeating it five times adds load and noise.

---

## 6. Backoff, Jitter, and Budgets

Use exponential backoff capped by the operation deadline and add randomness so clients do not synchronize:

```text
maximum delay for attempt n = min(cap, base × 2ⁿ)
actual delay = random value between 0 and maximum delay
```

`Retry-After` can communicate delay for responses such as `429` or `503`; clients still cap it by their deadline and policy.

Budgets exist at several levels:

- Per-call attempt limit
- End-to-end time budget
- Per-user/provider retry quota
- Fleet-wide additional-load budget

If every layer retries three times, one user action can multiply into 27 downstream calls. Pick one layer to own retries for a dependency path or coordinate budgets explicitly.

---

## 7. Python Client Pattern

This example retries only idempotent reads and keeps one end-to-end deadline:

```python
import asyncio
import random
import time

import httpx


RETRYABLE_STATUS = {408, 429, 502, 503, 504}


async def get_order(
    client: httpx.AsyncClient,
    order_id: str,
    *,
    total_timeout: float = 5.0,
    max_attempts: int = 3,
) -> dict[str, object]:
    deadline = time.monotonic() + total_timeout

    for attempt in range(max_attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Order lookup exceeded its total deadline")

        try:
            response = await client.get(
                f"/orders/{order_id}",
                timeout=httpx.Timeout(min(remaining, 2.0)),
            )
            if response.status_code not in RETRYABLE_STATUS:
                response.raise_for_status()
                return response.json()
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
            if attempt == max_attempts - 1:
                raise

        if attempt == max_attempts - 1:
            response.raise_for_status()

        maximum_delay = min(0.1 * (2**attempt), 1.0)
        delay = random.uniform(0.0, maximum_delay)
        if delay >= deadline - time.monotonic():
            raise TimeoutError("No retry budget remains")
        await asyncio.sleep(delay)

    raise RuntimeError("unreachable")
```

Create one long-lived `AsyncClient` with explicit pool limits. More complete timeout, limiter, circuit-breaker, and retry patterns live in [Safe and Scalable API Calls](../../fundamentals/fastapi/safe_and_scalable_api_calls/README.md).

---

## 8. Reliability Checklist

- Conditional writes prevent lost updates.
- Unsafe retryable operations require idempotency identity.
- Side effects follow durable transitions and are deduplicated.
- Clients distinguish known failure from unknown outcome.
- Retry policy lists exact operations and failure classes.
- One end-to-end deadline bounds all attempts and downstream work.
- Backoff has jitter and retry amplification is measured.
- Reconciliation exists after retry retention expires.

---

## References

- [HTTP Semantics — RFC 9110](https://www.rfc-editor.org/rfc/rfc9110)
- [Additional HTTP Status Codes — RFC 6585](https://www.rfc-editor.org/rfc/rfc6585)

---

**Next**: [Caching and Performance](07_caching_and_performance.md)
