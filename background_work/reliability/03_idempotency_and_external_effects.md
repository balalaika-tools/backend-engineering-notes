# Idempotency Makes an Ambiguous External Effect Recoverable

> **Who this is for**: Engineers whose worker may retry after a payment, email, object write, or provider call succeeded remotely but not locally.

Before reading this, understand why a new lease token cannot protect a provider call in **[Leases, Heartbeats, and Fencing](02_leases_heartbeats_and_fencing.md)**.

---

## 1. The hardest retry starts after the provider succeeded

The provider creates a payment, the worker dies before recording completion, and the queue redelivers the message. From local state alone, the second worker cannot distinguish “the request never arrived” from “the effect committed and only the response was lost.”

```text
provider accepted effect
  → worker died before durable completion
  → message redelivered
  → same business operation runs again
  → duplicate payment without idempotency
```

End-to-end exactly-once execution is not a realistic promise across a database, network, queue, and provider. The practical contract is at-least-once delivery with a **stable operation key** that makes repeated attempts converge on one business effect.

> **The near-miss**: deduplicating `message_id` stops one delivery from being handled twice. It does not protect republished messages, manual redrive, a new job record, or a crash after an external effect. Business idempotency uses a key whose scope outlives all those transports.

---

## 2. The key identifies business meaning, not an attempt

For editorial generation:

```text
workflow_run_id = run-42
step            = generate_final
definition      = 3
operation_key   = run-42:generate_final:v3

message_id      = msg-18, msg-19, ...
attempt_token   = token-a, token-b, ...
```

The operation key stays stable across publication, delivery, lease recovery, and manual redrive. Include a version or business scope only when a changed version legitimately represents a new effect.

| Effect | Stable scope | Replay-safe mechanism |
|---|---|---|
| Payment capture | Order + capture purpose | Provider idempotency key and local operation record |
| Final report generation | Workflow run + artifact type + definition | Provider key and deterministic object path |
| Webhook | Domain event ID + destination | Receiver inbox or sender delivery record |
| Database mutation | Domain command ID | Unique constraint or conditional update |
| Email | Notification purpose + recipient + source event | Provider dedup key when supported; otherwise explicit duplicate policy |
| Object write | Immutable artifact identity | Deterministic key plus content hash |

Attempt numbers must never enter the business key. They answer “who is trying now,” not “which effect is this?”

---

## 3. A request hash prevents key reuse from lying

A caller reuses `order-7:capture` with amount 900 after the original amount 700 succeeded. Returning the cached response would claim that the new request succeeded when the provider actually processed a different request.

Hash a canonical representation of every field that defines the operation:

```python
import hashlib
import json
from typing import Any


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


if __name__ == "__main__":
    left = request_hash({"amount": 700, "currency": "EUR"})
    reordered = request_hash({"currency": "EUR", "amount": 700})
    changed = request_hash({"amount": 900, "currency": "EUR"})
    assert left == reordered
    assert left != changed
    print(left)
```

Normalize domain values before hashing: currency case, integer minor units, omitted/default fields, and ordered versus unordered collections. Do not hash secrets that should not be retained; store a keyed digest when an ordinary hash would expose a low-entropy value.

---

## 4. The local operation record owns the reuse decision

```sql
CREATE TABLE provider_operations (
    idempotency_key TEXT PRIMARY KEY,
    workflow_run_id UUID NOT NULL,
    step TEXT NOT NULL,
    state TEXT NOT NULL,                 -- INTENT | SUCCEEDED | FAILED_PERMANENT
    request_hash TEXT NOT NULL,
    provider_operation_id TEXT,
    provider_response JSONB,
    result_ref TEXT,
    first_attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

First, insert intent without calling the provider:

```sql
INSERT INTO provider_operations (
    idempotency_key, workflow_run_id, step, state, request_hash
)
VALUES (
    :key, :run_id, 'generate_final', 'INTENT', :request_hash
)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING idempotency_key;
```

Whether the insert wins or loses, read the row and compare hashes **before** reading a cached response or calling the provider:

```sql
SELECT state, request_hash, provider_operation_id, provider_response, result_ref
FROM provider_operations
WHERE idempotency_key = :key;
```

Application logic follows one decision table:

| Existing row | Hash relation | Decision |
|---|---|---|
| None | — | Insert `INTENT`, then call provider |
| `INTENT` | Same | Another attempt may be in flight or crashed; retry later, or safely call the provider with the same key under current job ownership |
| `SUCCEEDED` | Same | Return the persisted response/result without a provider call |
| `FAILED_PERMANENT` | Same | Return the persisted terminal failure |
| Any | Different | Reject as idempotency-key conflict before response lookup or provider call |

> **Key insight**: An idempotency key is a promise that one identifier has one meaning. The request hash enforces the meaning; the provider key enforces the effect.

---

## 5. Provider retry and local persistence form one recovery loop

Send the same key and same request on every attempt. A provider with durable idempotency returns the first operation instead of creating another one. Persist the returned terminal result before completing the workflow:

```sql
UPDATE provider_operations
SET state = 'SUCCEEDED',
    provider_operation_id = :provider_operation_id,
    provider_response = :provider_response,
    result_ref = :result_ref,
    updated_at = now()
WHERE idempotency_key = :key
  AND request_hash = :request_hash
  AND state = 'INTENT'
RETURNING provider_operation_id, result_ref;
```

If a concurrent same-key attempt already persisted `SUCCEEDED`, this update returns zero; reload and reuse that terminal row. If the provider offers lookup by idempotency key, reconciliation may query first. Otherwise repeat the exact request with the stable key and let the provider return its original outcome.

The safe order is:

```text
local INTENT
  → provider call with stable key
  → local SUCCEEDED + provider ID + result
  → conditional workflow completion
```

The old dead-end—having a `provider_operation_id` recovery branch without any earlier write capable of storing that ID—is avoided. The ID arrives with the provider response and is persisted in the shown terminal update; an ambiguous pre-response crash recovers through the stable key.

---

## 6. A runnable crash test counts effects, not HTTP attempts

This in-memory provider simulates “effect committed, response handling crashed.” It receives two calls but creates one effect:

```python
from dataclasses import dataclass
import hashlib
import json


class IdempotencyConflict(ValueError):
    pass


@dataclass
class Operation:
    request_hash: str
    state: str = "INTENT"
    response: dict[str, str] | None = None


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.effects: dict[str, dict[str, str]] = {}

    def generate(self, key: str, prompt: str) -> dict[str, str]:
        self.calls += 1
        if key not in self.effects:
            self.effects[key] = {"provider_id": "gen-884", "artifact": f"final:{prompt}"}
        return self.effects[key]


def digest(prompt: str) -> str:
    encoded = json.dumps({"prompt": prompt}, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def generate_once(
    operations: dict[str, Operation],
    provider: FakeProvider,
    key: str,
    prompt: str,
    crash_after_provider: bool = False,
) -> dict[str, str]:
    current_hash = digest(prompt)
    operation = operations.setdefault(key, Operation(request_hash=current_hash))
    if operation.request_hash != current_hash:
        raise IdempotencyConflict("same key was used for a different request")
    if operation.state == "SUCCEEDED":
        assert operation.response is not None
        return operation.response

    response = provider.generate(key, prompt)
    if crash_after_provider:
        raise RuntimeError("worker crashed after provider effect")
    operation.state = "SUCCEEDED"
    operation.response = response
    return response


if __name__ == "__main__":
    operations: dict[str, Operation] = {}
    provider = FakeProvider()
    key = "run-42:generate_final:v3"

    try:
        generate_once(operations, provider, key, "verified draft", crash_after_provider=True)
    except RuntimeError:
        pass

    recovered = generate_once(operations, provider, key, "verified draft")
    assert recovered["provider_id"] == "gen-884"
    assert provider.calls == 2
    assert len(provider.effects) == 1

    try:
        generate_once(operations, provider, key, "changed draft")
    except IdempotencyConflict:
        pass
    else:
        raise AssertionError("different request reused the key")

    assert provider.calls == 2  # Conflict happened before the provider.
    print("2 attempts, 1 effect, mismatched replay rejected")
```

The production integration test makes the same assertions against a provider stub: durable result is terminal, external effect count is one, and different-hash reuse makes zero external calls.

---

## 7. Provider support changes how much local machinery is needed

A provider key may be enough for a simple stateless proxy when the provider retains keys for the whole replay window, returns the original result on reuse, and no local workflow must reconcile the outcome. A local operation record is still needed when you must validate the request hash, correlate a provider ID, expose progress, finish downstream state, recover after provider-key expiry, or audit the effect.

When the provider has no idempotency support:

- Prefer a conditional resource API (`PUT` to a deterministic ID) or deterministic object key.
- Put a receiver-side inbox/unique constraint at an endpoint you control.
- Serialize through one local operation record, while acknowledging that a crash during an opaque provider call remains ambiguous.
- Define an explicit duplicate/compensation policy when the effect cannot be made idempotent, as with some email delivery.

Retention must cover the maximum of message retention, retry/redrive window, client replay window, and provider idempotency retention. If full responses are too expensive or sensitive to retain, keep a tombstone with key, request hash, terminal status, and expiry policy. Reject a replay whose evidence has expired rather than silently treating it as new.

⚠️ Deleting idempotency rows while old messages remain redrivable turns a late duplicate into a new operation.

⚠️ Returning `INTENT` as success hides an ambiguous provider outcome. Report in-progress/unknown and reconcile it.

Do not add idempotency storage to pure read operations or deterministic local recomputation with no externally visible effect. Do not claim exactly-once execution merely because one transport offers duplicate suppression.

**How you know it is working**: the ratio of attempts to effects can exceed one during failure injection, while effects per operation key remains exactly one and hash-conflict count is observable.

---

**Next**: [Retries, Timeouts, and Cancellation](04_retries_timeouts_and_cancellation.md)
