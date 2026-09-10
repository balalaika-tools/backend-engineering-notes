# A Python Client Must Make Delivery and Shutdown Explicit

> **Who this is for**: Python developers with the local broker from the fundamentals quick start.

## Run one complete Python round trip

Install the native-backed client and save this as `kafka_round_trip.py`:

```bash
uv add confluent-kafka
```

```python
import json
import time
from confluent_kafka import Consumer, Producer

from event_contract import validate_event

delivery = {"ack": None, "error": None}


def delivered(error, message):
    delivery["error" if error else "ack"] = error or (message.partition(), message.offset())


producer = Producer({"bootstrap.servers": "localhost:9092"})
payload = {
    "event_id": "evt-101", "event_type": "order.created", "schema_version": 1,
    "occurred_at": "2026-09-04T09:15:00Z", "producer": "orders-api",
    "data": {"order_id": "ord-42", "currency": "EUR", "total_minor": 2590},
}
validate_event(payload)  # reject contract drift before it becomes retained data
producer.produce("orders", key="ord-42", value=json.dumps(payload), callback=delivered)
remaining = producer.flush(10)
if remaining or delivery["error"]:
    raise RuntimeError(f"broker delivery failed: remaining={remaining}, error={delivery['error']}")
if delivery["ack"] is None:
    raise RuntimeError("no delivery acknowledgment was observed")
print("delivered", *delivery["ack"])

consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "notes-demo-v1",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
deadline = time.monotonic() + 15
try:
    consumer.subscribe(["orders"])
    while time.monotonic() < deadline:
        message = consumer.poll(1.0)
        if message is None:
            continue
        if message.error():
            raise RuntimeError(f"broker/client error while polling: {message.error()}")
        event = json.loads(message.value())
        validate_event(event)  # consumers distrust retained bytes too
        print("consumed", message.partition(), message.offset(), event["data"]["order_id"])
        consumer.commit(message=message, asynchronous=False)
        break
    else:
        raise TimeoutError("no unread record arrived within 15s; check topic, group, and offsets")
finally:
    consumer.close()
```

Run `uv run python kafka_round_trip.py`. **Success signal:** it prints `delivered <partition>
<offset>` from the broker acknowledgment followed by `consumed <partition> <offset> ord-42`.
Rerunning with the same group consumes only records after its committed position. A delivery
failure names the broker error; a poll failure names the client error; and no unread record exits
after 15 seconds with a topic/group/offset diagnostic instead of hanging.

---

## 1. `produce` enqueues locally before the broker acknowledges

The producer batches asynchronously. A successful `produce()` call means the local client accepted
the record, not that Kafka stored it. `flush()` drives the callback in this short script, while the
callback supplies the broker acknowledgment or delivery error. A service should poll delivery
callbacks and flush during bounded shutdown rather than flush every record.

---

## 2. Commit only after the effect you are checkpointing

The example prints, then commits synchronously. Replace `print` with business processing and keep
the commit after success. This yields at-least-once processing: a crash after the effect but before
the commit repeats the event, so real effects need [idempotency](../reliability/02_idempotence_transactions_and_exactly_once.md).

> **Production:** add authentication, contract validation, bounded polling, structured error
> handling, metrics, and lifecycle integration before putting this loop in a service.

---

## 3. Async frameworks do not make the native client async

`confluent-kafka` performs network I/O in native code but exposes polling and callbacks that need
deliberate lifecycle integration. Do not run an infinite consumer loop inside a FastAPI request.
Start a dedicated worker process or application-lifespan task, and ensure shutdown stops polling,
finishes bounded work, commits safe positions, and closes the consumer.

> **Key insight**: Kafka client calls often cross a local buffer before they cross the network, so
> API return values and delivery acknowledgment are different events.

---

## 4. What breaks, and when not to embed a consumer

⚠️ Exiting without draining delivery callbacks can lose records still buffered only in the producer
process. The tell is accepted application requests with no broker record and no delivery error log.

Do not embed a long-lived consumer in every web replica when web autoscaling should not change
consumer-group membership. Deploy a separate worker when scaling and failure domains differ.

---

**Next**: [Processing Loops, Backpressure, and Shutdown](03_processing_loops_backpressure_and_shutdown.md)
