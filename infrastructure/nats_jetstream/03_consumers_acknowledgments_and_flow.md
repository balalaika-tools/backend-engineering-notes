# Consumer State Creates Redelivery and Backpressure

> **Who this is for**: engineers designing workers that must survive slow processing and crashes.

## One unacknowledged delivery explains the mechanism

```text
t=0s   consumer fetches stream sequence 41; delivery count = 1
t=8s   database commit succeeds
t=9s   process crashes before Ack
t=30s  AckWait expires; sequence 41 is eligible for delivery count = 2
```

The stream did not duplicate the event. The **consumer** kept sequence 41 pending because it never
received an acknowledgment. Redelivery is the mechanism behind JetStream's normal at-least-once
delivery.

---

## 1. Pull consumers make demand and capacity explicit

A **pull consumer** stores delivery state on the server while clients request bounded batches.
Prefer it for new worker services: each fetch expresses current capacity, multiple instances can
share one durable consumer, and idle workers do not accumulate a client-side push backlog.

```text
ORDERS stream → durable order-worker → fetch(max_messages=20, expires=2s)
                                      ├── worker A
                                      └── worker B
```

Both workers share the durable's progress. Give analytics a different durable consumer rather than
a different worker queue name on the same consumer. The official
[pull-consumer guide](https://docs.nats.io/learn/jetstream/pull-consumers) develops the protocol in depth.

> **Core:** replicas of one logical worker share one durable pull consumer and request only as much
> work as their bounded concurrency can accept.

> **The near-miss**: a durable consumer is not a process identity. It is shared server-side state;
> replicas of one logical worker use the same durable name, while independent applications use
> different names.

---

## 2. Acknowledgment verbs are state transitions

Most workers need only three outcomes:

| Worker outcome | Action | Consumer consequence |
|---|---|---|
| Side effect committed | `Ack` | Mark this delivery handled |
| Transient failure | `Nak` or no ack | Redeliver, immediately or after delay |
| Processing legitimately takes longer | `InProgress` | Extend the acknowledgment timer |

`Term` stops redelivery for the message and should be reserved for a deliberate terminal policy,
usually paired with an observable dead-letter or incident path. `AckSync` waits for the server to
confirm the acknowledgment when the client must know it was persisted.

Acknowledging before the business effect creates possible loss. Acknowledging after it creates a
duplicate crash window; [Reliable Effects](05_reliable_effects_and_publishing.md) shows how to close
that window with idempotency.

---

## 3. Four bounds prevent one worker from owning the stream

- `AckWait` is the initial processing lease; set it above normal processing time or send progress.
- `BackOff` defines redelivery delays and overrides the simple `AckWait` redelivery schedule.
- `MaxDeliver` bounds attempts but does not itself move failed work to another stream.
- `MaxAckPending` caps delivered-but-unacknowledged messages and therefore bounds in-flight work.

For 20 concurrent tasks that usually finish in 2 seconds and may take 15 seconds, a starting policy
might use `MaxAckPending=20`, `AckWait=30s`, and delayed retries of `5s, 30s, 5m`. The values must
follow measured task latency and downstream capacity, not broker throughput.

> **Key insight**: consumer configuration is a distributed lease and admission-control policy, not
> just a way to select messages.

---

## 4. Filtering and start position create a view, not a new log

A consumer filter such as `orders.eu.>` delivers only matching messages from `ORDERS`. Its delivery
policy chooses the initial cursor: all stored data, only new data, a sequence, or a time. Neither
operation copies the stream.

Changing a durable's filter or start semantics can be restricted once it exists. Treat durable
consumer configuration as deployed infrastructure: declare it, diff it, and migrate intentionally
instead of letting every process invent it at startup.

> **Production:** use a stable durable name, explicit filter, finite `MaxAckPending`, bounded fetches,
> and an alert on both pending and redelivered counts.

---

## 5. What breaks, and when push is still appropriate

⚠️ `AckWait` shorter than real processing time causes concurrent redelivery while the first attempt
is still running. Rising redeliveries with successful work is the tell.

⚠️ `MaxDeliver` is not a dead-letter queue. When attempts are exhausted, the message remains in the
stream but ordinary delivery stops; monitor advisories and implement an explicit recovery path.

Do not use a durable pull consumer for a live UI feed where disconnected viewers should miss old
updates; Core NATS or an ephemeral consumer is a better match. Push consumers remain useful for
low-latency, continuously connected subscribers, but make flow control and slow-consumer behavior
part of the design.

---

**Next**: [Async Python Services](04_async_python_services.md)
