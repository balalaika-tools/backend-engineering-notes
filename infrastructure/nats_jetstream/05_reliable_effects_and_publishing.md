# Reliability Ends at the Business Effect, Not the Broker Ack

> **Who this is for**: engineers whose JetStream messages change durable application state.

## One crash window defeats “exactly once” marketing

```text
1. worker receives evt-202
2. charges card successfully
3. process crashes before acknowledging
4. JetStream redelivers evt-202
5. worker charges the card again
```

The broker behaved correctly: it could not know whether step 2 committed. JetStream can suppress
duplicate publishes within a window and confirm persisted acknowledgments, but it cannot make an
arbitrary payment API, email, or database transaction happen exactly once.

---

## 1. Stable event identity makes retries recognizable

Give each logical event an immutable `event_id` generated before its first publish. Send the same ID
in the `Nats-Msg-Id` header on retries:

```python
from nats.aio.msg import Msg

ack = await js.publish(
    "orders.created",
    payload,
    headers={"Nats-Msg-Id": "evt-202"},
)
```

Within the stream's configured duplicate window, a repeated ID is acknowledged without storing a
second copy. This is **publish deduplication**. It covers uncertainty such as “the server stored my
first attempt, but its acknowledgment was lost.” It does not deduplicate forever and does not
protect a downstream side effect. The official
[advanced publishing guide](https://docs.nats.io/learn/jetstream/advanced-publishing) shows the
message-ID and expected-sequence mechanisms.

Record the ID inside the payload as well. Protocol headers may not survive export to another system,
and consumers need the identity at the business boundary.

---

## 2. Idempotent effects close the consumer crash window

An **idempotent effect** makes repeated attempts for the same event converge on one durable result.
For a database-backed handler, insert the event ID and change business state in one transaction:

```sql
BEGIN;

WITH accepted AS (
    INSERT INTO processed_events (consumer, event_id)
    VALUES ('billing-projection', 'evt-202')
    ON CONFLICT DO NOTHING
    RETURNING 1
)
UPDATE order_projection
SET status = 'created'
WHERE order_id = 'ord-84'
  AND EXISTS (SELECT 1 FROM accepted);

COMMIT;
```

The common table expression exposes whether the insert won. On redelivery, the uniqueness
constraint makes `accepted` empty, so the update becomes a no-op and the worker can safely
acknowledge it. `(consumer, event_id)` allows independent projections to process the same event once
each.

For an external API, pass the event ID as that API's idempotency key when supported. If the remote
system offers no idempotent operation or status lookup, no broker setting can remove the uncertainty.

> **Core:** acknowledge after the effect commits, and make replay of the same stable event ID
> converge on that already-committed result.

> **Key insight**: delivery guarantees describe movement between broker and client; effect
> guarantees require state at the boundary where the business change commits.

---

## 3. The transactional outbox fixes database-to-broker dual writes

The producer has the mirror-image problem:

```text
commit order row → crash → publish never happens
publish event → crash → order transaction rolls back
```

Write the order and an **outbox row** in one database transaction. A relay publishes unsent rows
with `Nats-Msg-Id=event_id`, records the publish acknowledgment, and retries ambiguous attempts.
Broker deduplication contains close retries; consumers still use idempotent effects because retries
outside the duplicate window and operational replay remain possible.

See [Atomic transitions and outbox](../../background_work/reliability/01_atomic_transitions_and_outbox.md)
for the canonical transaction schema and relay lifecycle.

---

## 4. Ack confirmation narrows one broker-side uncertainty

A normal `Ack` is fire-and-forget. A synchronous or “double” acknowledgment waits for JetStream to
confirm it processed the ack. Combined with publish deduplication, NATS documentation calls this an
exactly-once message-delivery pattern. Interpret the boundary precisely: it prevents certain
duplicate storage and acknowledgment uncertainties; it does not include external effects.

Use confirmation when replaying an acknowledged message is materially expensive. It adds a network
round trip, so ordinary idempotent projections may prefer a normal ack and tolerate redelivery.

> **Production:** test termination after receiving, during the effect, after commit, and during ack.
> The success signal is one final business effect with a handled consumer position after every run.

---

## 5. What breaks, and when not to chase exactly once

⚠️ Generating a fresh `event_id` on every publish retry defeats broker deduplication and consumer
idempotency while producing no error.

⚠️ A dedup table insert committed separately from the business update can record “done” before the
effect exists. Keep both in one transaction.

Do not add dedup state for naturally convergent operations such as replacing a projection row with
the complete latest value. Do not describe a workflow as exactly once when it crosses a system that
has neither idempotency keys nor reconciliation; state the remaining ambiguity instead.

> **Edge case:** optimistic concurrency with expected stream sequence can serialize publishers to a
> stream or subject, but it is not a replacement for database transaction isolation.

---

**Next**: [Operations and Recovery](06_operations_and_recovery.md)
