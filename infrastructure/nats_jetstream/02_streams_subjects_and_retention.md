# Streams Turn Subject Traffic into Bounded Stored Data

> **Who this is for**: engineers who have completed the first durable round trip and need to design a stream.

## One event, three independent decisions

For `orders.eu.created`, a stream configuration answers three different questions:

```text
subjects:  orders.>       → should this message enter the stream?
retention: limits         → what event permits its removal?
limits:    1 hour / 100MB → how much history may remain?
```

The event's subject selects storage, the retention policy defines why messages leave, and resource
limits bound growth. Treating those as one “retention setting” is how teams accidentally delete
work or keep data forever.

---

## 1. Subject boundaries are part of the data model

NATS subjects are dot-separated routing names. `*` matches one token and `>` matches the remaining
tokens, so `orders.*.created` matches `orders.eu.created` but not `orders.eu.priority.created`;
`orders.>` matches both.

A useful subject carries stable routing dimensions without embedding unbounded values:

```text
orders.eu.created          # bounded region and event type
orders.customer.8f31...    # poor default: creates high-cardinality permissions and filters
```

One message can match only one stream in a JetStream account. Overlapping stream subject sets are
rejected because two stores claiming the same subject would make ownership ambiguous. Use multiple
consumers with filters when readers need different views of the same stored data.

---

## 2. Retention policy changes the meaning of acknowledgment

Start with **LimitsPolicy** for event history: messages remain until age, bytes, count, or
per-subject limits evict them. Consumer acknowledgments advance that consumer but do not delete the
event.

| Policy | Message removal condition | Good fit | Dangerous assumption |
|---|---|---|---|
| **LimitsPolicy** | Configured limits | Replayable event history | “Ack deletes the message” |
| WorkQueuePolicy | First matching consumer successfully acknowledges | Competing durable work | Multiple independent consumers can replay |
| InterestPolicy | All consumers with interest acknowledge | Fan-out work with bounded membership | A future consumer can read old data |

This is a semantic choice, not merely a storage optimization. With WorkQueuePolicy, acknowledging
an order may make it unavailable to every future reader. The official
[retention guide](https://docs.nats.io/learn/jetstream/retention-policies) walks through these policies.

> **Core:** use LimitsPolicy unless removal on acknowledgment is an explicit part of the workload contract.

---

## 3. Limits form an intersection, not fallbacks

Given `max_age=1h`, `max_msgs=1_000_000`, and `max_bytes=100MB`, JetStream removes data when any
active bound requires it. A burst can hit 100 MB in five minutes even though the age window promises
an hour under normal traffic.

Choose limits from a measured envelope:

```text
required bytes ≈ peak messages/second × average stored bytes × replay seconds × headroom
```

For 200 messages/s, 1 KiB stored per message, one hour of replay, and 1.5× headroom, plan roughly
1.1 GB—not 100 MB. Measure actual stream bytes because headers, indexes, and workload distribution
make the estimate incomplete.

> **Key insight**: retention policy says *why* a message may leave; limits say *when capacity forces it to leave*.

---

## 4. Discard policy decides how a full stream fails

`DiscardOld` admits new messages and evicts the oldest eligible data. `DiscardNew` rejects new
messages once a limit is reached. For an audit trail, rejected writes may be safer than silent loss
of history; for a rolling telemetry window, evicting old samples is usually intentional.

Publish through JetStream and inspect its acknowledgment so `DiscardNew` is visible to the caller.
A Core NATS publish has no persistence acknowledgment and cannot prove the stream accepted the data.

> **Production:** set explicit `max_age` and `max_bytes` even when another limit appears sufficient.
> Monitor both utilization and rejected publishes before the boundary is reached.

---

## 5. What breaks, and when not to use one stream

⚠️ A broad `>` subject captures system traffic and unrelated application messages. The symptom is
unexpected storage growth and authorization scope, not a matching error.

⚠️ Raising `max_age` without increasing disk capacity shortens effective retention when `max_bytes`
wins first. Watch the oldest stored timestamp, not configuration alone.

Do not combine workloads with different ownership, retention, replication, or access rules in one
stream. Split audit events from disposable jobs even if both originate in the same service.

> **Edge case:** `MaxMsgsPerSubject` and subject transforms are useful for state-like streams, but
> use the higher-level Key/Value abstraction when the real model is “latest value by key.”

---

**Next**: [Consumers, Acknowledgments, and Flow](03_consumers_acknowledgments_and_flow.md)
