# Webhook Producer Design

> **Who this is for**: Teams building outbound webhook infrastructure that must survive transactions, endpoint failures, and fleet scale. Assumes [Delivery Model and Event Contracts](01_delivery_model_and_event_contracts.md).

---

## 1. Producer Responsibilities

A producer owns more than `httpx.post()`:

```text
event creation → subscription match → durable delivery → scheduling
→ safe endpoint resolution → signing → HTTP attempt → classification
→ retry/dead-letter/replay → customer visibility and support
```

Run this path outside the business request whenever possible. A slow customer endpoint must not hold open checkout, billing, or account updates.

---

## 2. Subscription Model

A subscription needs:

```text
id
owner tenant/account
HTTPS endpoint URL
selected event types
status: pending_verification | active | paused | disabled
signing key reference and rotation state
payload/schema version
created/updated metadata
failure counters and last delivery state
```

Subscription API controls:

- Authenticate and authorize owner/administrator.
- Allow only known event types and minimum required events.
- Validate URL syntax and run the SSRF policy in [Signatures, Security, and SSRF](04_signatures_security_and_ssrf.md).
- Verify endpoint ownership with a challenge or first signed test event where appropriate.
- Return the signing secret once, then store it encrypted or in a secret manager.
- Audit endpoint, event selection, key rotation, pause, and deletion changes.
- Revalidate DNS/egress policy on every connection, not only registration.

Avoid placing credentials in callback URLs.

---

## 3. Transactional Outbox

The dual-write bug occurs when business state commits but publishing fails, or publishing occurs before the transaction later rolls back.

```text
❌ commit invoice ── process crashes ── webhook event never recorded
❌ send webhook ── database rollback ── consumer sees a fact that never committed
```

Write business state and an outbox event in one database transaction:

```sql
BEGIN;

UPDATE invoices
SET status = 'paid', paid_at = now()
WHERE tenant_id = :tenant_id
  AND id = :invoice_id
  AND status = 'open';

INSERT INTO webhook_events (
    id, tenant_id, event_type, schema_version, subject, occurred_at, payload
) VALUES (
    :event_id, :tenant_id, 'invoice.paid', 1,
    :subject, now(), CAST(:payload AS jsonb)
);

COMMIT;
```

An outbox relay reads committed events and creates per-subscription deliveries. Make that step idempotent with a unique constraint:

```sql
CREATE UNIQUE INDEX webhook_deliveries_event_subscription_uq
ON webhook_deliveries (event_id, subscription_id);
```

The relay can run more than once without creating duplicate logical deliveries. HTTP attempts may still duplicate and keep the same event/delivery identity.

---

## 4. Delivery State Machine

```text
pending → attempting
             ├── 2xx → delivered
             ├── transient → retry_scheduled → attempting
             ├── terminal → failed
             └── exhausted → dead_letter

paused subscription holds pending/retry deliveries by policy
manual replay creates a new attempt, not a new business event
```

Persist at least:

- Event and subscription IDs
- Delivery ID and current state
- Attempt count, next attempt time, first/last attempt time
- Last response status, bounded/redacted response diagnostic
- Failure category and final reason
- Request duration and resolved destination metadata useful for audit

Do not store full customer response bodies indiscriminately. They may contain secrets or attacker-controlled high-volume content. Cap, sanitize, and restrict access.

---

## 5. Scheduler and Worker Concurrency

Claim due rows atomically:

```sql
SELECT id
FROM webhook_deliveries
WHERE state IN ('pending', 'retry_scheduled')
  AND next_attempt_at <= now()
ORDER BY next_attempt_at, id
FOR UPDATE SKIP LOCKED
LIMIT :batch_size;
```

Update claimed rows to an attempting state/lease within the transaction, then perform network I/O after commit. A lease timeout recovers work from crashed workers.

Partition or fairly schedule by destination/tenant so one failed endpoint cannot consume every worker. Apply:

- Global worker concurrency
- Per-tenant and per-destination concurrency
- Per-destination rate limit
- Connection pool limits
- Short connect and response-header/body limits
- Circuit/open-endpoint suppression with scheduled probes

Never hold a database transaction open during the customer HTTP request.

---

## 6. HTTP Attempt Contract

Send exact signed bytes with explicit metadata:

```http
POST /hooks/billing HTTP/1.1
Host: consumer.example
Content-Type: application/json
User-Agent: Example-Webhooks/1
Webhook-Id: evt_01J8Q9M4D7...
Webhook-Timestamp: 1784716200
Webhook-Signature: v1,...

{"id":"evt_01J8Q9M4D7","type":"invoice.paid","schema_version":1,"occurred_at":"2026-07-22T10:30:00Z","producer":"billing","subject":"invoice/inv_123","data":{"invoice_id":"inv_123","amount_minor":4200,"currency":"EUR"}}
```

Recommended request behavior:

- HTTPS only, verified certificate and hostname.
- No automatic redirects, or an equally strict revalidation policy for every redirect hop.
- Explicit connect/write/read/pool timeouts.
- Fixed maximum response bytes; the response body is normally diagnostic only.
- Stable event/delivery headers and content type.
- Signature computed over the exact bytes transmitted.

Interpret responses under a published policy. A reasonable default is:

| Outcome | Producer action |
|---------|-----------------|
| Any documented `2xx` | Mark accepted/delivered |
| Network timeout/reset | Retry within policy; outcome may be ambiguous |
| `408`, `429`, selected `5xx` | Retry with bounded backoff; honor safe `Retry-After` policy |
| Other `4xx` | Usually terminal/configuration failure; provider policy may allow limited retries |
| `410 Gone` | Candidate to disable endpoint after explicit documented policy |

Do not assume every provider follows the same response policy. Publish yours.

---

## 7. Retry, Dead Letter, and Replay

Use exponential backoff with jitter, capped by maximum delivery age. A typical schedule spreads attempts from seconds to hours; the correct window follows the business need and capacity model.

After exhaustion:

- Keep a dead-letter record for a documented retention period.
- Notify endpoint owners through a separate channel.
- Expose delivery/attempt history and safe diagnostics.
- Allow authorized replay of selected failures.
- Preserve original event ID/body; generate new attempt identity and signature timestamp.

Manual replay must not bypass current endpoint security checks, subscription ownership, rate limits, or payload access policy.

---

## 8. Event and Schema Evolution

- Pin a subscription or endpoint to a documented payload version, or version event types explicitly.
- Make additive fields safe and instruct consumers to ignore unknowns.
- Never change units/meaning under the same field and version.
- Preserve the original event representation for replay; do not silently reserialize an old event using today's schema.
- Test old payloads through current signing and delivery code.

If sensitive fields become newly available, require an explicit subscription/version upgrade rather than adding them to every existing endpoint.

---

## 9. Producer Checklist

- Event creation shares a transaction with the business change.
- Delivery creation and worker claim are idempotent/leased.
- Customer endpoints never block the business request.
- SSRF policy and TLS verification run for every attempt.
- Queues, connections, retries, response sizes, and diagnostics are bounded.
- Fairness prevents one tenant/destination from exhausting the fleet.
- Replay preserves event identity and historical schema.
- Owners can inspect, test, rotate, pause, and diagnose safely.

---

**Next**: [Webhook Consumer Design](03_consumer_design.md)
