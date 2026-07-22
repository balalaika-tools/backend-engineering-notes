# Webhook Retries, Idempotency, Ordering, and Replay

> **Who this is for**: Producers and consumers defining honest delivery guarantees and recovery behavior. Assumes the preceding webhook chapters.

---

## 1. Why Duplicates Are Normal

```text
producer                  consumer
  │── event evt_1 ─────────>│ verify + durable insert
  │                         │── 204 response ──X lost
  │ timeout                 │
  │── retry evt_1 ─────────>│ duplicate insert ignored
  │<── 204 ─────────────────│
```

The producer cannot distinguish “request never arrived” from “response was lost after acceptance.” Retrying creates duplicates; not retrying creates loss. At-least-once delivery chooses duplicates and requires idempotent consumers.

---

## 2. Failure Classification

Producer policy should classify outcomes instead of retrying everything:

| Failure | Likely class | Action |
|---------|--------------|--------|
| DNS/connect/TLS timeout | Transient or configuration | Retry within age budget; surface diagnostics |
| Read timeout | Ambiguous | Retry same event/delivery identity |
| `2xx` | Accepted | Mark delivered |
| `400`/`401`/`403` | Payload/signature/configuration | Usually terminal or very limited retry; alert owner |
| `404` | Endpoint path/configuration | Usually terminal/limited retry by published policy |
| `408`/`429` | Temporary | Retry with capped backoff/guidance |
| `410` | Endpoint intentionally gone | Disable/fail according to explicit policy |
| `5xx` | Receiver/dependency failure | Retry with backoff |

Consumers and networks differ, so publish exact behavior. Never allow an endpoint to keep a worker occupied indefinitely.

---

## 3. Retry Schedule

Use exponential backoff with full jitter:

```text
maximum_delay(n) = min(cap, base × 2ⁿ)
delay(n) = random(0, maximum_delay(n))
```

Bound retries by both attempt count and delivery age. Add fairness and per-destination concurrency so one broken endpoint does not starve healthy customers.

If honoring `Retry-After`:

- Parse only supported forms.
- Cap it to provider policy and remaining retention.
- Treat it as untrusted input.
- Do not hold a worker during the wait; persist `next_attempt_at`.

Retry storms often happen when a popular consumer recovers. Jitter attempts, gradually release backlog, and cap destination traffic even after it becomes healthy.

---

## 4. Event, Delivery, and Attempt Identity

```text
event_id      immutable business fact, used by consumer deduplication
delivery_id   event + subscription destination
attempt_id    one network exchange
```

On automatic retry:

- Keep event ID and event body.
- Keep delivery ID.
- Create/increment attempt identity.
- Generate a new signed timestamp/signature as the scheme requires.

On manual replay:

- Keep original event ID and occurrence time.
- Usually create a new attempt under the existing delivery or an explicitly linked replay delivery.
- Expose actor, reason, and time in audit history.

Generating a new event ID during replay defeats consumer deduplication and can repeat financial/business effects.

---

## 5. Consumer Idempotency

The inbox unique constraint deduplicates event receipt, but downstream effects need the same protection.

### Prefer state assignment

```text
✅ set local invoice status/version from authoritative provider object
❌ increment paid_invoice_count for every delivery attempt
```

### Guard side effects transactionally

```sql
BEGIN;

INSERT INTO processed_provider_events (producer, event_id, processed_at)
VALUES (:producer, :event_id, now())
ON CONFLICT DO NOTHING
RETURNING event_id;

-- Only continue when the INSERT returned a row.
UPDATE invoices
SET provider_status = :status,
    provider_version = :version
WHERE provider_invoice_id = :invoice_id
  AND provider_version < :version;

INSERT INTO notification_outbox (
    id, event_type, source_event_id, payload, created_at
) VALUES (
    :notification_id,
    'invoice.payment_recorded',
    :event_id,
    CAST(:notification_payload AS jsonb),
    now()
)
ON CONFLICT (source_event_id) DO NOTHING;

COMMIT;
```

The notification is also written to an outbox so a worker crash cannot update the invoice without eventually sending the intended local notification.

Choose deduplication retention at least as long as provider event replay retention plus operational delay. If records expire sooner, old manual replays can repeat effects.

---

## 6. Ordering

Webhooks often have no global ordering guarantee. Even if the producer sends sequentially, retries and multiple workers can reorder arrival.

```text
event v10 attempt fails
event v11 succeeds
retry v10 arrives after v11
```

Strategies:

| Strategy | Fit |
|----------|-----|
| Version check / ignore stale | Resource snapshots with monotonic provider version |
| Partition by subject | High-volume ordered aggregates; still handle gaps |
| Buffer gap briefly | Sequence matters and missing event likely arrives soon |
| Fetch current object | Provider read API is authoritative |
| Commutative operation | Sets/max/merge designed to be order-independent |

Timestamps do not establish a reliable total order. Prefer producer sequence/version scoped to a documented subject/partition.

If there is no ordering guarantee, event handlers must not require `created` before `updated`. Fetch or upsert missing local state.

---

## 7. Replay vs Retry

| Property | Automatic retry | Manual replay |
|----------|-----------------|---------------|
| Trigger | Delivery policy | Operator/API/customer action |
| Timing | Scheduled within retry window | May be much later |
| Purpose | Recover transient attempt failure | Repair incident/configuration/processing gap |
| Security | Current endpoint policy | Current policy plus authorization/audit |
| Identity | Same event/delivery | Same event; replay attempt/delivery linked |

Replay controls:

- Filter by subscription, event type, time, and failure state.
- Preview count/data classification.
- Require capability and audit actor/reason.
- Rate-limit and schedule fairly.
- Preserve original bytes/schema where promised.
- Make cancellation/progress visible for large replay jobs.

---

## 8. Reconciliation

Correctness-critical integrations need a pull repair path:

```text
webhooks provide low-latency hints
scheduled reconciliation queries authoritative API
  → list resources changed since checkpoint
  → compare versions/state
  → repair missing/stale local projection
  → advance checkpoint only after durable processing
```

Checkpoints and time windows must overlap enough to tolerate clock skew and delayed indexing; deduplication handles repeated reads. Prefer provider cursors or monotonic update tokens to raw local timestamps.

Reconciliation also detects silently disabled endpoints, exhausted deliveries, consumer bugs that returned `2xx`, and retention gaps.

---

## 9. Delivery Semantics Checklist

- `2xx` acknowledgement meaning is explicit.
- Retryable statuses/failures, backoff, attempts, and maximum age are published.
- Event/delivery/attempt IDs keep their distinct roles.
- Replays preserve event identity and are audited/rate-limited.
- Consumer inbox and side effects are idempotent for the full replay window.
- Ordering scope and stale/gap behavior are documented.
- Correctness-critical data has an authoritative reconciliation path.
- “Exactly once” is not claimed without a precise scope and retention boundary.

---

## References

- [Stripe webhook delivery behaviors](https://docs.stripe.com/webhooks)
- [GitHub webhook redelivery and event identity](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)

---

**Next**: [Webhook Testing, Observability, and Operations](06_testing_observability_and_operations.md)
