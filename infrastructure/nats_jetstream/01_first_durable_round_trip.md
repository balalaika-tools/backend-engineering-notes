# Store and Replay Your First JetStream Message

> **Who this is for**: backend engineers who can run Docker but have not used NATS.

## Quick start: one server, one stream, one durable consumer

Start NATS Server with **JetStream**—the persistence layer that stores selected NATS messages—and
mount its data directory so a container restart does not erase the exercise:

```bash
docker run --rm -d --name nats-notes \
  -p 4222:4222 -p 8222:8222 \
  -v nats-notes-data:/data \
  nats:2.14.6 -js -sd /data
```

Use the official CLI image on the server container's network namespace. Create `ORDERS`, a
**stream** (server-side message store) that captures the subject pattern `orders.>`:

```bash
docker run --rm --network container:nats-notes natsio/nats-box:0.19.7 \
  nats stream add ORDERS --subjects 'orders.>' --storage file \
  --retention limits --max-age 1h --max-bytes 104857600 --defaults

docker run --rm --network container:nats-notes natsio/nats-box:0.19.7 \
  nats pub orders.created \
  '{"event_id":"evt-101","order_id":"ord-42","type":"order.created"}'

docker run --rm --network container:nats-notes natsio/nats-box:0.19.7 \
  nats consumer add ORDERS order-worker \
  --pull --deliver all --ack explicit --wait 30s --max-deliver 5 --defaults

docker run --rm --network container:nats-notes natsio/nats-box:0.19.7 \
  nats consumer next ORDERS order-worker --count 1 --ack
```

**Success signal:** the final command prints the `ord-42` payload and reports that the message was
acknowledged. `nats stream info ORDERS` reports one stored message. If stream creation says
JetStream is unavailable, inspect `docker logs nats-notes`; the common silent mismatch is starting
the server without `-js`.

> **Production:** this is a plaintext, single-node learning setup. Replication, credentials,
> storage sizing, and recovery belong in [Operations](06_operations_and_recovery.md) and
> [Security](07_security_and_multitenancy.md).

---

## 1. Persistence decouples the publisher from the consumer in time

Core NATS publish-subscribe sends a message only to subscribers that are connected at that moment.
The `ORDERS` stream changes that outcome: the server matches `orders.created` against `orders.>`,
stores the message, and assigns a stream sequence before any consumer needs to exist. This is the
time-decoupling described in the [official JetStream overview](https://docs.nats.io/concepts/jetstream).

```text
publisher ── orders.created ──> ORDERS stream, sequence 1
                                      │
                                      └── later ──> order-worker
```

A **subject** is the routing name carried by a NATS message. A stream name is not a publish
destination: publishers still send to subjects, and a stream stores messages whose subjects match
its configured patterns.

> **Core:** publish to a subject, verify the stream captured it, and treat the publish acknowledgment
> as the evidence of durable acceptance.

---

## 2. A consumer owns progress; consuming does not necessarily delete data

The durable `order-worker` is a **consumer**—server-side delivery state over a stream. It remembers
which messages are pending, acknowledged, or due for redelivery. Run the final `consumer next`
command again: it waits because sequence 1 is already acknowledged for this consumer.

Create another durable consumer and the stored event is available again:

```bash
docker run --rm --network container:nats-notes natsio/nats-box:0.19.7 \
  nats consumer add ORDERS audit-reader \
  --pull --deliver all --ack explicit --defaults
docker run --rm --network container:nats-notes natsio/nats-box:0.19.7 \
  nats consumer next ORDERS audit-reader --count 1 --ack
```

The changed consumer name creates an independent cursor; it does not republish the event.

> **Key insight**: a stream owns stored messages, while each consumer owns an independent view of
> delivery progress over those messages.

---

## 3. What breaks first

⚠️ A subject that does not match any stream can still be accepted by Core NATS and then disappear.
`nats pub payments.created ...` may report success while `ORDERS` remains unchanged. A JetStream
publish API adds a server acknowledgment and is the safer application path.

⚠️ Removing the Docker volume destroys the only stored copy. A stopped container is recoverable;
`docker volume rm nats-notes-data` is not.

---

## 4. When not to use this setup

Do not use one local server for availability tests, throughput conclusions, shared environments, or
production. Do not add JetStream when messages are intentionally ephemeral and only live listeners
matter; Core NATS is simpler for that contract.

Clean up the container with `docker stop nats-notes`. Keep the volume if you want the next start to
recover the stream. Success is `docker ps` no longer listing `nats-notes`.

---

**Next**: [Streams, Subjects, and Retention](02_streams_subjects_and_retention.md)
