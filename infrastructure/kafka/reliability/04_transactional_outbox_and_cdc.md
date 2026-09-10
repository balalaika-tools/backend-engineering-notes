# An Outbox Turns Two Writes into One Recoverable Handoff

> **Who this is for**: services that update a database and publish a corresponding event.

## The dual-write gap

`COMMIT order` followed by `publish order.created` can crash between calls, leaving durable business
state with no event. Reversing the calls creates an event for a database change that may roll back.

---

## 1. Store business state and intent in one database transaction

```text
BEGIN → insert orders row → insert outbox(event_id, payload, unpublished) → COMMIT
                                      ↓
                         relay/CDC publishes to Kafka
```

The relay may publish twice around its own crash, so consumers still deduplicate by `event_id`.
**Change data capture (CDC)** reads database-log changes; a polling relay claims outbox rows directly.

PostgreSQL can own the business row and publication intent in one commit:

```sql
CREATE TABLE orders (
    order_id text PRIMARY KEY,
    total_minor bigint NOT NULL CHECK (total_minor >= 0)
);

CREATE TABLE outbox_events (
    event_id uuid PRIMARY KEY,
    aggregate_id text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);

CREATE INDEX outbox_unpublished ON outbox_events (created_at)
WHERE published_at IS NULL;
```

The application uses parameters and one database transaction; no SQL string contains event data:

```python
import json
import uuid


def create_order(conn, order_id: str, total_minor: int) -> str:
    event_id = str(uuid.uuid4())
    payload = {"event_id": event_id, "order_id": order_id, "total_minor": total_minor}
    with conn.transaction():
        conn.execute(
            "INSERT INTO orders (order_id, total_minor) VALUES (%s, %s)",
            (order_id, total_minor),
        )
        conn.execute(
            """INSERT INTO outbox_events
               (event_id, aggregate_id, event_type, payload)
               VALUES (%s, %s, %s, %s::jsonb)""",
            (event_id, order_id, "order.created", json.dumps(payload)),
        )
    return event_id
```

After commit, either both rows exist or neither does. Querying `outbox_events` for the returned ID
is the success signal; an order with no matching outbox row means the writes escaped the shared
transaction.

---

## 2. Ownership decides between polling and CDC

Polling is simple and application-owned but adds query load and cleanup. CDC scales integration and
captures ordered database changes but introduces connector, log-retention, and schema-operational
dependencies.

A polling relay claims a small batch with `FOR UPDATE SKIP LOCKED`, publishes each event using
`event_id` as the Kafka key, waits for the broker acknowledgment, and only then sets
`published_at`. Keep claimed rows locked only for a bounded batch:

```text
t0  relay-a claims evt-101 (published_at=NULL)
t1  Kafka acknowledges evt-101
t2  relay-a crashes before UPDATE               → row remains unpublished
t3  relay-b claims and publishes evt-101 again  → possible duplicate, no loss
t4  relay-b sets published_at and commits        → cleanup may later archive the row
```

The duplicate is intentional evidence of an uncertain acknowledgment boundary. A consumer's
unique `event_id` constraint collapses both deliveries to one effect. Retain published rows through
the maximum reconciliation window; deleting them immediately removes the evidence needed to
compare database intent with Kafka output.

**Success signal:** crash after database commit and before publish; the relay later emits the event.
Then crash after publish and verify a duplicate causes one downstream effect.

> **Key insight**: the outbox does not make database and Kafka atomic; it records a durable promise
> inside the database so publication can be retried until observed.

---

## 3. What breaks, and when not to use an outbox

⚠️ Deleting outbox rows before confirmed publication turns relay failure into permanent event loss.

Do not add an outbox when Kafka is already the authoritative input and all outputs remain inside one
Kafka transaction. Use the smaller atomic boundary.

---

**Next**: [Testing Kafka Services](05_testing_kafka_services.md)
