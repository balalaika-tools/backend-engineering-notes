# Webhook Delivery Model and Event Contracts

> **Who this is for**: Engineers designing or consuming event callbacks between independently operated HTTP systems. Start with [API Fundamentals](../01_api_fundamentals.md).

---

## 1️⃣ What a Webhook Is

A **webhook** is an HTTP callback: a provider sends an HTTP request to a consumer-controlled endpoint when an event occurs.

```text
consumer                         provider
  │ POST callback URL + events    │
  │──────────────────────────────>│ subscription
  │                               │
  │                     business event occurs
  │                               │
  │<──────── signed HTTP POST ────│ delivery attempt
  │────────── 2xx receipt ───────>│
```

The callback avoids frequent polling and does not require a persistent connection. It introduces a harder boundary: the provider calls an untrusted, independently deployed server that may be slow, unavailable, duplicated, or maliciously configured.

A webhook is not a message broker. HTTP supplies one delivery attempt; the webhook product must add event identity, durable state, signatures, retries, replay, limits, and observability.

---

## 2️⃣ Vocabulary

| Term | Meaning |
|------|---------|
| Producer/provider | System where the event originates and that sends callbacks |
| Consumer | System receiving and processing callbacks |
| Endpoint | Consumer URL receiving deliveries |
| Subscription | Configuration mapping events to an endpoint and secret/key |
| Event | Immutable statement that something happened |
| Delivery | One event destined for one subscription/endpoint |
| Attempt | One HTTP exchange for a delivery |
| Acknowledgement | Consumer's HTTP response that the producer interprets as received/accepted |
| Replay/redelivery | Operator- or API-triggered new attempt for an existing event/delivery |
| Reconciliation | Reading provider state to repair missed, delayed, or out-of-order events |

Keep IDs distinct. One event may create many deliveries; one delivery may have many attempts.

```text
event evt_1
├── delivery del_A to subscription A
│   ├── attempt 1 timeout
│   └── attempt 2 204
└── delivery del_B to subscription B
    └── attempt 1 410
```

---

## 3️⃣ Event Envelope

Use a stable envelope around type-specific data:

```json
{
  "id": "evt_01J8Q9M4D7...",
  "type": "invoice.paid",
  "schema_version": 1,
  "occurred_at": "2026-07-22T10:30:00.123Z",
  "producer": "billing",
  "subject": "invoice/inv_123",
  "tenant_id": "tenant_7",
  "data": {
    "invoice_id": "inv_123",
    "payment_id": "pay_456",
    "amount_minor": 4200,
    "currency": "EUR"
  }
}
```

| Field | Contract role |
|-------|---------------|
| `id` | Stable event identity for deduplication and support |
| `type` | Stable routing discriminator, usually noun plus past-tense fact |
| `schema_version` | Payload compatibility boundary |
| `occurred_at` | When the business fact occurred, not when this attempt was sent |
| `producer` | Owning source namespace |
| `subject` | Entity/aggregate the event concerns |
| `tenant_id` | Routing context when safe to expose; never trusted as authorization by itself |
| `data` | Type-specific snapshot or reference |

Delivery metadata such as attempt number and signature timestamp belongs in delivery headers or a separate delivery context, not in the immutable event body. Replaying the same event should keep the same event ID and business occurrence time.

---

## 4️⃣ Event Semantics

Events are facts:

```text
✅ invoice.paid
✅ subscription.cancelled
✅ report.completed

❌ update_invoice        command, not fact
❌ invoice.changed       too vague unless change contract is explicit
❌ sync                  no stable domain meaning
```

Document:

- Exact trigger and transaction boundary
- Whether it is emitted once per transition or update
- Snapshot vs changed fields vs object reference
- Authorization/data classification
- Expected duplicates and ordering scope
- Retention and replay window
- Schema compatibility rules
- How to retrieve current authoritative state

An event ID identifies the fact, not necessarily the business object. An invoice may produce many events.

---

## 5️⃣ Thin vs Full Payloads

### Thin event

```json
{"id":"evt_1","type":"invoice.paid","data":{"invoice_id":"inv_123"}}
```

Consumer fetches current state from the read API.

✅ Small, less stale data, simpler event evolution, authorization centralized in read API.

⚠️ Extra request, dependency on API availability, and current state may have advanced beyond the event.

### Full snapshot event

Contains enough data to process without another call.

✅ Lower latency and independent processing.

⚠️ Larger sensitive payload, more schema coupling, and snapshot may be stale by delivery time.

### Changed-fields event

Compact but hardest to use safely: consumers need prior state and perfect ordering. Prefer a snapshot or a reference unless delta semantics are a genuine domain requirement.

---

## 6️⃣ Delivery Lifecycle

```text
business transaction
  → persist event in outbox
  → create deliveries for matching subscriptions
  → schedule attempt
  → sign exact bytes
  → HTTP POST
     ├── accepted → delivered
     ├── transient failure → scheduled retry
     ├── terminal failure → failed/disabled policy
     └── attempts/age exhausted → dead letter
  → operator/API may replay
```

A `2xx` acknowledges whatever the consumer contract says—normally that the event was validated and stored durably, not that all business side effects finished.

> **Key insight**: A producer's successful HTTP response proves only that the receiving endpoint returned that response. It does not prove downstream processing succeeded.

---

## 7️⃣ Delivery Guarantees

Network ambiguity and retries mean practical webhook delivery is usually **at-least-once** within a retention/attempt policy:

- Events can be delayed.
- Attempts can be duplicated.
- Different events can arrive out of order.
- Delivery can stop after retry exhaustion.
- Manual replay can occur much later.

Consumers deduplicate by event ID and make effects idempotent. Producers expose delivery history/replay. Both sides use a read API or periodic reconciliation for correctness-critical state.

Do not advertise “real-time guaranteed exactly once.” State measurable guarantees instead:

- Percentage delivered within a target time
- Retry duration and maximum attempts
- Event/replay retention
- Ordering scope, if any
- Maximum payload size
- Endpoint timeout and accepted responses

---

## 8️⃣ Webhooks vs Alternatives

| Need | Better fit |
|------|------------|
| Notify external partner with only an HTTPS server | Webhook |
| Durable internal event processing with managed consumers | Broker/queue |
| Frequent bidirectional browser interaction | WebSocket |
| Consumer cannot expose an endpoint | Poll/read API |
| Low-frequency change with strongest simplicity | Conditional polling |
| Correctness-critical synchronization | Webhook plus reconciliation/read API |

Webhooks and a read API complement each other: push gives low-latency hints; pull repairs gaps and retrieves authoritative state.

---

## References

- [GitHub webhook best practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)
- [Standard Webhooks specification](https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md)

---

**Next**: [Webhook Producer Design](02_producer_design.md)

