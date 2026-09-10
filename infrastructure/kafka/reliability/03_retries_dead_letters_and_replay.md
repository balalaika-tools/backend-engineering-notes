# Recovery Paths Must Preserve the Evidence of Failure

> **Who this is for**: engineers handling transient failures and poison records.

## Retry by failure class, not by exception count

A timeout may succeed on retry; an invalid currency will not. Retry transient failures with bounded
backoff. Route a repeatedly unprocessable record to a **dead-letter topic (DLT)** with its original
topic, partition, offset, key, payload reference, error class, and attempt count.

```text
orders → consumer → transient: retry topic → consumer
                  └ permanent: orders.dlt → investigation → controlled replay
```

Use one envelope through the whole path so recovery never loses provenance:

```json
{
  "event_id": "evt-101",
  "source": {"topic": "orders", "partition": 2, "offset": 8},
  "attempt": 1,
  "first_failed_at": "2026-09-10T09:00:00Z",
  "next_attempt_at": "2026-09-10T09:00:05Z",
  "error_class": "PaymentGatewayTimeout",
  "payload": {"order_id": "ord-42", "currency": "EUR", "total_minor": 2590}
}
```

Bound the policy: attempts 1–3 use delays of 5, 30, and 120 seconds; attempt 4 writes the envelope
to `orders.dlt` with `next_attempt_at=null`. The observable record path is:

```text
orders[2]@8 → retry.5s(attempt=1) → retry.30s(attempt=2)
            → retry.120s(attempt=3) → orders.dlt(attempt=4)
```

---

## 1. Retry topics trade ordering for availability

Moving offset 8 aside lets offset 9 proceed, so entity order can change. If order is required, block
the partition with bounded retries or isolate failing keys through another design.

---

## 2. Replay is a production write operation

Replay with a new consumer group or explicit offsets only after checking retention, schema support,
downstream idempotency, rate limits, and expected volume. Record who initiated it and its bounds.

After correcting the bad dependency or payload, replay only the audited slice into a staging topic:

```bash
kafka-console-consumer.sh --bootstrap-server localhost:9092 \
  --topic orders.dlt --partition 0 --offset 0 --max-messages 1 --timeout-ms 10000 \
  --property print.key=true --property key.separator=$'\t' \
  | kafka-console-producer.sh --bootstrap-server localhost:9092 \
      --topic orders.replay.reviewed --property parse.key=true --property key.separator=$'\t'
```

The replay bounds are DLT partition 0, DLT offset 0, and one record; the envelope independently
preserves source partition 2 and source offset 8. First inspect the console output in a
non-production drill because console key formatting must match the chosen producer properties.
**Success signal:** `orders.replay.reviewed` receives one record with `event_id=evt-101`, while the
idempotency store still reports one business effect. If more than one record moves, the replay was
not bounded as intended.

**Success signal:** a deliberately invalid event reaches the DLT with provenance, and replaying it
after correction creates one effect. DLT depth alone silently hides events that lost their source
identity.

> **Key insight**: a dead-letter topic is evidence and a recovery queue, not successful handling.

---

## 3. What breaks, and when not to retry

⚠️ Unbounded immediate retries can pin a partition and overload the dependency already failing.

Do not retry validation errors, authorization denials, or permanent missing resources without a
specific state change that could make the next attempt succeed.

---

**Next**: [Transactional Outbox and CDC](04_transactional_outbox_and_cdc.md)
