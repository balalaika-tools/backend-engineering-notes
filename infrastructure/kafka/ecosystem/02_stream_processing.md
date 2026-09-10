# Stream Processing Materializes Continuously Changing Answers

> **Who this is for**: engineers deriving, joining, or aggregating event streams.

## A window changes an unbounded stream into a bounded question

“Count payments” never finishes. “Count accepted payments per merchant in five-minute **event-time
windows**”—buckets selected by timestamps inside the events—defines keys, time boundaries,
late-arrival policy, and an output update model. A **watermark** is the engine's estimate of how far
event time has advanced; a **grace period** keeps a window revisable for a bounded interval after
its nominal end.

Take merchant `m-7`, a five-minute window `[10:00, 10:05)`, a two-minute grace period, and a
watermark of `10:06`—the engine's belief that ordinary event time has advanced that far:

```text
arrival  event timestamp  record       window state / emitted update
10:01    10:01            pay-1, €10   count=1, total=€10  → emit (1, €10)
10:04    10:03            pay-2, €20   count=2, total=€30  → emit (2, €30)
restart  —                restore      state remains (2, €30) from changelog/checkpoint
10:06    10:02            pay-3, €5    within grace        → emit correction (3, €35)
10:08    10:04            pay-4, €9    past 10:07 close    → late/drop side output
```

The event timestamp, not arrival time, selects the window. `pay-3` updates an already emitted
answer because it arrives before the grace deadline; changing only its arrival to `10:08` moves it
to the late-record policy. Recovery is correct only if restart restores `(2, €30)` before new input.

---

## 1. State appears as soon as output depends on history

Filtering one record is stateless. Counts, joins, deduplication, and sessions require a **state
store**, durable per-key working data restored after failure; partition-compatible keys; a recovery
**changelog**, a replicated record of state updates, or checkpoint; and retention. Event time uses
the timestamp carried by the event;
**processing time** uses when the application sees it. Late events make those answers diverge.

Kafka Streams is the native Java library; Flink and other engines provide broader distributed
processing models. Python services can consume and produce directly, but should not recreate a
stateful engine casually.

**Success signal:** feed on-time and deliberately late records and observe the documented window
updates after restart. A correct happy-path count silently avoids the hard time semantics.

> **Key insight**: a streaming computation is a maintained state machine whose correctness depends
> on keys, time, recovery, and output semantics—not a loop that happens to run forever.

---

## 2. What breaks, and when not to stream

⚠️ Joining streams partitioned by different keys produces incomplete or expensive results unless
one side is repartitioned deliberately.

Use batch SQL when minutes or hours of latency are acceptable and recomputation is simpler than
continuous state. Use a database view when all inputs already live transactionally in one database.

---

**Next**: [Share Groups and Queue Semantics](03_share_groups_and_queue_semantics.md)
