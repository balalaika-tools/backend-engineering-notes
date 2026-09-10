# Safe Consumers Bound Work Before They Commit Progress

> **Who this is for**: engineers turning a demo consumer into a long-running worker.

## The failure: polling outruns processing

A consumer fetches 500 records and launches 500 coroutines against a database pool of 20. Memory
grows, timeouts cascade, polling stalls, and the group reassigns the partition while old work still
runs. The fix is bounded in-flight work plus partition-aware commits.

---

## 1. Backpressure keeps fetched work within downstream capacity

Use a semaphore or bounded queue sized from the actual downstream bottleneck. Pause assigned
partitions when capacity is full and resume them after work completes, while continuing the client
heartbeats required by its protocol.

```text
poll → bounded queue (100) → workers (20) → database
          full: pause partitions       success: mark offset complete
```

Rate limits and concurrency limits solve different problems: a rate limit bounds work per time;
concurrency bounds simultaneous resource occupancy.

---

## 2. Parallel completion cannot commit through a gap

Offsets 10, 11, and 12 run concurrently. If 11 fails while 12 succeeds, committing 13 would skip 11
on restart. Track completed offsets and advance the commit frontier only through the highest
contiguous success—in this case, 11 until offset 11 succeeds.

For simpler correctness, process each partition sequentially and parallelize across partitions.
Add within-partition concurrency only when order is irrelevant and the commit-frontier complexity
is justified.

---

## 3. Shutdown is a protocol, not a signal handler

On termination: stop accepting new work, keep group membership alive if possible, finish within a
deadline, commit only contiguous successes, then close the consumer. If the deadline expires,
abandon unfinished work and rely on idempotent replay.

**Success signal:** terminate the worker during a controlled slow event; after restart, every
unfinished event reappears and no completed effect is missing. Low lag alone cannot prove this.

> **Key insight**: consumer concurrency is safe only when ownership, completion, and committed
> progress remain aligned at partition granularity.

---

## 4. Assemble the lifecycle in one bounded worker

This worker allows out-of-order completion but advances each partition's commit frontier only
through contiguous successes. Save it as `bounded_worker.py`; set `KAFKA_TOPIC` and `KAFKA_GROUP`
to test-specific values.

```python
import json
import os
import signal
import threading
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor

from confluent_kafka import Consumer, TopicPartition

MAX_IN_FLIGHT = 100
RESUME_AT = 50
SHUTDOWN_SECONDS = 3
stop = threading.Event()


def handle(raw: bytes) -> None:
    event = json.loads(raw)
    print("started", event["event_id"], flush=True)
    time.sleep(event.get("work_ms", 0) / 1000)
    # A real effect must deduplicate by event_id before this can be parallel and replay-safe.
    print("effect", event["event_id"], flush=True)


class Frontier:
    def __init__(self) -> None:
        self.next_offset: dict[tuple[str, int], int] = {}
        self.done: dict[tuple[str, int], set[int]] = defaultdict(set)

    def observe(self, topic: str, partition: int, offset: int) -> None:
        self.next_offset.setdefault((topic, partition), offset)

    def complete(self, topic: str, partition: int, offset: int) -> None:
        self.done[(topic, partition)].add(offset)

    def committable(self) -> list[TopicPartition]:
        result = []
        for key, frontier in list(self.next_offset.items()):
            while frontier in self.done[key]:
                self.done[key].remove(frontier)
                frontier += 1
            if frontier != self.next_offset[key]:
                self.next_offset[key] = frontier
                result.append(TopicPartition(key[0], key[1], frontier))
        return result


consumer = Consumer({
    "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"),
    "group.id": os.environ["KAFKA_GROUP"],
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
pool = ThreadPoolExecutor(max_workers=20)
frontier = Frontier()
pending: dict[tuple[str, int, int], Future] = {}
paused = False
shutdown_deadline: float | None = None


def reap() -> None:
    for position, future in list(pending.items()):
        if not future.done():
            continue
        del pending[position]
        topic, partition, offset = position
        try:
            future.result()
        except Exception as error:
            print("handler-failed", topic, partition, offset, repr(error), flush=True)
            stop.set()  # leave this offset and every later gap uncommitted for replay
        else:
            frontier.complete(topic, partition, offset)
    offsets = frontier.committable()
    if offsets:
        consumer.commit(offsets=offsets, asynchronous=False)


def drain(deadline: float) -> None:
    while pending and time.monotonic() < deadline:
        reap()
        time.sleep(0.05)
    reap()  # commit only completed offsets; unfinished futures remain behind the frontier


def on_assign(client, partitions) -> None:
    print("assigned", [(p.topic, p.partition) for p in partitions], flush=True)
    client.assign(partitions)


def on_revoke(client, partitions) -> None:
    revoked = {(p.topic, p.partition) for p in partitions}
    deadline = shutdown_deadline or (time.monotonic() + SHUTDOWN_SECONDS)
    while any((t, p) in revoked for t, p, _ in pending) and time.monotonic() < deadline:
        reap()
        time.sleep(0.05)
    reap()
    if any((t, p) in revoked for t, p, _ in pending):
        stop.set()  # do not process a new assignment while revoked work is still running
    else:
        for key in revoked:
            frontier.next_offset.pop(key, None)
            frontier.done.pop(key, None)
    print("revoked", sorted(revoked), flush=True)
    client.unassign()


signal.signal(signal.SIGTERM, lambda *_: stop.set())
signal.signal(signal.SIGINT, lambda *_: stop.set())

try:
    consumer.subscribe([os.environ["KAFKA_TOPIC"]], on_assign=on_assign, on_revoke=on_revoke)
    while not stop.is_set():
        reap()
        assignment = consumer.assignment()
        if len(pending) >= MAX_IN_FLIGHT and not paused:
            consumer.pause(assignment)
            paused = True
        elif paused and len(pending) <= RESUME_AT:
            consumer.resume(assignment)
            paused = False

        message = consumer.poll(0.25)  # polling continues while partitions are paused
        if message is None:
            continue
        if message.error():
            raise RuntimeError(message.error())
        position = (message.topic(), message.partition(), message.offset())
        frontier.observe(*position)
        pending[position] = pool.submit(handle, message.value())
finally:
    shutdown_deadline = time.monotonic() + SHUTDOWN_SECONDS
    drain(shutdown_deadline)
    consumer.close()
    unfinished = len(pending)
    pool.shutdown(wait=False, cancel_futures=True)
    print("shutdown", "unfinished", unfinished, flush=True)
    if unfinished:
        os._exit(2)  # thread work cannot be force-cancelled; end the process at the deadline
```

The consumer alone calls client APIs; worker threads only perform the handler. Pause/resume bounds
memory, polling preserves membership, `Frontier` refuses to commit through a failed or unfinished
offset, rebalance callbacks drain revoked ownership, and the process enforces the hard deadline
even though Python cannot force-cancel a running thread.

Use a fresh topic and group for this termination drill. Publish a slow record followed by a fast
record, start the worker with output redirected to `worker.log`, wait until `started evt-slow`
appears, then send `SIGTERM`:

```bash
export KAFKA_TOPIC="orders.worker-test-$RANDOM" KAFKA_GROUP="worker-test-$RANDOM"
docker exec kafka-notes /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --create --topic "$KAFKA_TOPIC" \
  --partitions 1 --replication-factor 1
printf '%s\n' \
  '{"event_id":"evt-slow","work_ms":10000}' \
  '{"event_id":"evt-fast","work_ms":0}' | \
  docker exec -i kafka-notes /opt/kafka/bin/kafka-console-producer.sh \
    --bootstrap-server localhost:9092 --topic "$KAFKA_TOPIC"
uv run python bounded_worker.py >worker.log 2>&1 & worker_pid=$!
until grep -q 'started evt-slow' worker.log; do kill -0 "$worker_pid"; sleep 0.1; done
kill -TERM "$worker_pid"
wait "$worker_pid"
grep '^shutdown' worker.log
uv run python bounded_worker.py
```

**Success signal:** the first run prints `shutdown unfinished 1` or more after the three-second
deadline; the restart receives every offset at or after the first unfinished gap. An effect that
completed beyond that gap may repeat, so the real handler must deduplicate by `event_id`. If restart
begins after an unfinished offset, the frontier or commit call advanced too far.

> **Production:** add structured metrics, handler-specific retry policy, idempotent effects, and a
> test-controlled fault hook. The reusable crash harness is in
> [Testing Kafka Services](../reliability/05_testing_kafka_services.md).

---

## 5. What breaks, and when not to parallelize

⚠️ Committing the largest completed offset silently skips earlier unfinished records. The symptom is
a business gap with a healthy committed lag metric.

Do not parallelize within a partition when processing order is part of the invariant. Increase
partitions with a correct key or optimize the handler before weakening ordering.

---

**Next**: [Topic and Partition Design](04_topic_and_partition_design.md)
