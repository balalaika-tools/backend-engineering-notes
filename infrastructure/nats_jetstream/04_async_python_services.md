# Build Bounded Async Python Publishers and Workers

> **Who this is for**: Python backend engineers who understand streams and durable pull consumers.

## Minimal application: publish once, fetch once, acknowledge once

With the local server from [the first round trip](01_first_durable_round_trip.md) running, create a
temporary environment and install the official async client:

```bash
uv venv
uv pip install 'nats-py==2.15.0'
```

Save this complete example as `jetstream_round_trip.py`:

```python
import asyncio
import json

import nats
from nats.js.api import ConsumerConfig, DeliverPolicy


async def main() -> None:
    nc = await nats.connect("nats://127.0.0.1:4222")
    try:
        js = nc.jetstream()
        sub = await js.pull_subscribe(
            "orders.created",
            durable="python-worker",
            config=ConsumerConfig(
                deliver_policy=DeliverPolicy.NEW,
                ack_wait=30,
                max_deliver=5,
                max_ack_pending=10,
            ),
        )

        payload = {"event_id": "evt-202", "order_id": "ord-84"}
        ack = await js.publish("orders.created", json.dumps(payload).encode())
        print(f"stored stream={ack.stream} sequence={ack.seq}")

        messages = await sub.fetch(batch=1, timeout=2)
        for message in messages:
            event = json.loads(message.data)
            print(f"processed event_id={event['event_id']}")
            await message.ack()
    finally:
        await nc.drain()  # flush buffered protocol work before closing the socket


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run python jetstream_round_trip.py`. **Success signal:** it prints an `ORDERS` stream
sequence and `processed event_id=evt-202`. A publish timeout or `no response from stream` means the
subject is not captured by a stream, JetStream is disabled, or the client reached the wrong server.

This baseline intentionally omits schema validation, reconnection telemetry, idempotent effects,
and shutdown coordination. Add them before shipping; the next note owns the effect boundary.

---

## 1. A publish acknowledgment proves storage, not business completion

`js.publish` waits for a **publish acknowledgment** containing the stream and sequence. It proves a
matching stream accepted the message according to its replication policy. It does not prove a
consumer processed it or that a database side effect committed.

By contrast, `nc.publish` is a Core NATS operation. It can be useful for transient messages, but it
does not return evidence that JetStream stored the event.

> **Core:** use the JetStream publish API whenever application correctness depends on persistence.

---

## 2. One long-lived connection belongs to the application lifetime

A NATS connection multiplexes subscriptions and requests. Create it during service startup, share
it through dependency injection, and drain it during graceful shutdown. Connecting for every HTTP
request adds handshakes and makes reconnection behavior impossible to centralize.

```text
application startup → connect → publish/fetch for many requests → drain → process exit
```

`drain()` stops accepting new work and flushes buffered messages before close. It cannot make an
unfinished database transaction safe; stop fetch loops first, allow bounded in-flight work to
finish or release it for redelivery, and then drain the connection.

---

## 3. The fetch loop must enforce its own concurrency budget

A production loop should fetch no more work than it can run and acknowledge only after the effect
commits. This excerpt assumes `handle()` is idempotent as defined in the next note:

```python
async def run_worker(subscription, handle, stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        try:
            messages = await subscription.fetch(batch=10, timeout=1)
        except nats.errors.TimeoutError:
            continue  # an empty poll is normal; it is not a broker outage

        async with asyncio.TaskGroup() as tasks:
            for message in messages:
                tasks.create_task(process_one(message, handle))


async def process_one(message, handle) -> None:
    try:
        await handle(json.loads(message.data))
    except Exception:
        await message.nak(delay=5)  # avoid a hot retry loop against a failing dependency
        return
    await message.ack()  # the effect is durable before delivery progress advances
```

Bound `batch`, `MaxAckPending`, and application concurrency together. Fetching 1,000 messages into a
10-task worker only moves backlog from the server into process memory and starts acknowledgment
timers too early.

> **Key insight**: async makes waiting cheap; it does not create downstream capacity, so the fetch
> size and consumer limits must express a real concurrency budget.

---

## 4. What breaks, and when not to embed management

⚠️ Catching `TimeoutError` around both fetch and processing misclassifies a handler timeout as an
empty poll. Keep the fetch exception boundary narrow.

⚠️ Calling `ack()` in `finally` marks failed effects complete. The symptom is disappearing work
with an error log and no redelivery.

Do not let every application replica freely mutate stream policy. Provision stable streams and
durables through a controlled deployment step; application startup may assert compatible state and
fail loudly when it differs.

> **Edge case:** use `in_progress()` for genuinely long operations, but prefer smaller resumable
> work units when heartbeats would merely hide an unbounded task.

---

**Next**: [Reliable Effects and Publishing](05_reliable_effects_and_publishing.md)
