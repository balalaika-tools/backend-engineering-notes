# Webhook Consumer Design

> **Who this is for**: Teams receiving webhooks that must authenticate senders, absorb duplicates, and process events without request-time fragility. Assumes [Delivery Model](01_delivery_model_and_event_contracts.md).

---

## 1. The Safe Ingress Pipeline

```text
HTTP request
  → enforce transport/body limits
  → read raw bytes
  → verify signature and timestamp over raw bytes
  → parse and validate event envelope
  → authorize expected producer/subscription/account
  → insert event into durable inbox with unique event ID
  → return 2xx quickly
  → worker claims inbox row
  → idempotent domain processing
  → processed or retry/dead-letter state
```

Do not perform slow business work, send emails, call dependencies, or hold a large transaction before acknowledgement. Provider timeouts create retries and duplicate pressure.

Return success only after the event is durably accepted under your contract. Enqueuing into an in-memory task is not durable.

---

## 2. Verify the Exact Raw Body

Cryptographic signatures are byte-sensitive:

```text
signed:   {"amount":4200,"currency":"EUR"}
changed:  {"currency": "EUR", "amount": 4200}
```

Both JSON objects have equivalent data but different bytes and signatures. Read the body once, cap it, verify it, then parse those same bytes. Framework body parsers or middleware that reserializes JSON before verification will break valid signatures.

Use the provider's official verification library where available. The next chapter provides a complete HMAC example and rotation rules.

---

## 3. Durable Inbox and Deduplication

```sql
CREATE TABLE webhook_inbox (
    producer text NOT NULL,
    event_id text NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    payload jsonb NOT NULL,
    state text NOT NULL DEFAULT 'received',
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz,
    last_error_code text,
    processed_at timestamptz,
    PRIMARY KEY (producer, event_id)
);
```

Insert with conflict handling:

```sql
INSERT INTO webhook_inbox (
    producer, event_id, event_type, occurred_at, payload
) VALUES (
    :producer, :event_id, :event_type, :occurred_at, CAST(:payload AS jsonb)
)
ON CONFLICT (producer, event_id) DO NOTHING;
```

Return the same successful acknowledgement for a valid duplicate. If duplicates receive an error, the producer may retry indefinitely.

Scope uniqueness by provider/account when event IDs are not globally unique. Verify the documented identity property instead of assuming it.

---

## 4. Complete FastAPI Ingress

This endpoint uses PostgreSQL as a durable inbox. It imports the verifier implemented in [Signatures, Security, and SSRF](04_signatures_security_and_ssrf.md).

```python
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import asyncpg
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from webhook_security import VerificationError, verify_webhook


MAX_WEBHOOK_BYTES = 256 * 1024
SIGNING_SECRETS = [
    secret.encode("utf-8")
    for secret in os.environ["BILLING_WEBHOOK_SECRETS"].split(",")
]


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=200)
    schema_version: int = Field(ge=1)
    occurred_at: datetime
    producer: str
    data: dict[str, Any]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(
        dsn=os.environ["DATABASE_URL"],
        min_size=1,
        max_size=10,
        command_timeout=5.0,
    )
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(lifespan=lifespan)


@app.post("/webhooks/billing", status_code=204)
async def receive_billing_webhook(request: Request) -> Response:
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > MAX_WEBHOOK_BYTES:
                return Response(status_code=413)
        except ValueError:
            return Response(status_code=400)

    chunks: list[bytes] = []
    received_bytes = 0
    async for chunk in request.stream():
        received_bytes += len(chunk)
        if received_bytes > MAX_WEBHOOK_BYTES:
            return Response(status_code=413)
        chunks.append(chunk)
    raw_body = b"".join(chunks)

    message_id = request.headers.get("webhook-id")
    timestamp = request.headers.get("webhook-timestamp")
    signature = request.headers.get("webhook-signature")

    try:
        verify_webhook(
            body=raw_body,
            message_id=message_id,
            timestamp=timestamp,
            signature_header=signature,
            secrets=SIGNING_SECRETS,
        )
    except VerificationError:
        return Response(status_code=401)

    try:
        event = EventEnvelope.model_validate_json(raw_body)
    except ValidationError:
        return Response(status_code=422)

    if event.id != message_id or event.producer != "billing":
        return Response(status_code=400)

    payload = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
    async with request.app.state.pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO webhook_inbox (
                producer, event_id, event_type, occurred_at, payload
            ) VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (producer, event_id) DO NOTHING
            """,
            event.producer,
            event.id,
            event.type,
            event.occurred_at,
            payload,
        )

    return Response(status_code=204)
```

Do not trust a compressed request's `Content-Length` as the decompressed size. Prefer disabling content encoding for webhook endpoints or enforce a streaming decompressed limit at the trusted edge/application.

The database command timeout and pool are deliberately bounded. If durable storage is unavailable, return failure so the producer retries; do not acknowledge and lose the event.

---

## 5. Inbox Worker

Workers claim rows with a transaction and `FOR UPDATE SKIP LOCKED`, move them to a leased `processing` state, then perform work. On crash, an expired lease makes the row eligible again.

Handler design:

- Route by exact event type and supported schema version.
- Treat payload as untrusted even after signature verification.
- Load internal objects through tenant/account scope.
- Prefer state assignment/upsert over non-idempotent increments.
- Record provider event ID with each resulting side effect.
- Use an internal outbox for emails/messages triggered by the event.
- Mark processed in the same transaction as local state changes where possible.

```sql
INSERT INTO processed_provider_events (producer, event_id, processed_at)
VALUES (:producer, :event_id, now())
ON CONFLICT DO NOTHING;
```

A unique guard next to the side effect protects against worker lease races and operator replay.

---

## 6. Ordering and Authoritative State

Do not assume arrival order. Options:

- Ignore a stale version/sequence already superseded locally.
- Partition processing by subject while accepting gaps.
- Buffer briefly while waiting for a missing sequence, then reconcile.
- Fetch current provider state and treat webhook as an invalidation hint.

For payments, subscriptions, or access rights, retrieving the authoritative object is often safer than applying a delta from a possibly delayed event.

```text
receive invoice.paid for inv_123
  → deduplicate event
  → GET provider invoice inv_123 with authenticated client
  → set local projection to returned version/status
```

Still authenticate and store the webhook; otherwise an attacker can force arbitrary provider API reads.

---

## 7. Poison Events and Internal Retry

Separate producer delivery retry from consumer processing retry. Once durably accepted, your worker owns processing failures.

Classify:

- Unsupported/invalid schema: terminal quarantine and alert
- Missing dependency/transient timeout: retry with backoff
- Domain conflict: reconcile/current-state policy
- Code bug: bounded retries, quarantine, fix and replay
- Permanent authorization/configuration: terminal with operator action

Keep raw or normalized payload only for the required retention and protect it as customer data. Redact exception logs and cap last-error text.

---

## 8. Consumer Checklist

- Body limits apply before expensive parsing.
- Signature and timestamp cover exact raw bytes.
- Event identity and expected producer/account agree with the route/secret.
- Durable inbox insert precedes `2xx` acknowledgement.
- Duplicate valid events return success.
- Worker effects are idempotent and transactionally guarded.
- Ordering assumptions have a sequence/reconciliation strategy.
- Unsupported and poison events become visible quarantine, not infinite retry.
- Payload retention, encryption, and access follow data classification.

---

**Next**: [Webhook Signatures, Security, and SSRF](04_signatures_security_and_ssrf.md)
