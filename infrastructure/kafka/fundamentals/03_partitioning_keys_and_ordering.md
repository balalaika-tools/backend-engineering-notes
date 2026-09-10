# Keys Turn Ordering Requirements into Partition Placement

> **Who this is for**: engineers choosing event keys or reasoning about ordering.

## The failure that exposes a bad key

`order.created(ord-42)` reaches partition 0 while `order.cancelled(ord-42)` reaches partition 3.
Two consumers run independently, so cancellation finishes first. Kafka did not reorder either
partition; the producer failed to place related records in the same ordering domain.

---

## 1. A stable key keeps one entity's changes together

The producer serializes a key, hashes it, and maps it to a partition. Using `order_id` makes all
events for one order follow one partition's sequence:

```text
key=ord-42, created   ─┐
key=ord-42, paid      ├─ hash → partition 2 → offsets 17,18,19
key=ord-42, cancelled ─┘
```

The key is a routing and ordering choice, not merely metadata. A null key commonly distributes
records for throughput and provides no entity-level affinity.

Placement depends on the **serialized key bytes** and the client's partitioner, not the displayed
string alone. Before mixing client languages or changing serializers, publish the same fixture key
(`ord-42`) from each client and assert that the resulting partition matches. A mismatch means the
clients encode or hash differently; pin compatible serializers and partitioners before rollout.

---

## 2. The correct key follows the invariant

Choose the smallest stable identity across which relative order matters. For an account ledger,
that may be `account_id`; for order lifecycle events, `order_id`. A timestamp is a poor key because
it neither groups an entity nor prevents concurrent equal timestamps.

Hot entities create **hot partitions**: one celebrity account can dominate the partition holding
its key. Splitting the key increases throughput but deliberately weakens ordering, so the consumer
must then tolerate or reconstruct order.

> **Core:** key design is an application invariant expressed as infrastructure routing.

---

## 3. Adding partitions can change future key placement

Many partitioners compute placement using the current partition count. Increasing a topic from 6
to 12 partitions can send later records for `ord-42` somewhere different from earlier records. Old
records do not move automatically.

If stable lifetime placement matters, create a new topic and migrate deliberately, or introduce a
stable logical bucket in the keying scheme. Do not assume partition expansion is behavior-free.

---

## 4. Success and failure signals

**Success signal:** sample a single entity's events and verify they share a partition and increase
in business-valid order. The silent failure is checking only timestamps; timestamps can look sorted
even while retries or clock skew violate causal order.

⚠️ Idempotent producers prevent retry-induced duplicates within their scope, but they cannot fix an
incorrect key or ordering across partitions.

> **Key insight**: Kafka preserves the sequence it receives within one partition; the application
> decides which records deserve to share that sequence.

---

## 5. When not to require Kafka ordering

Do not force unrelated work through one partition merely to obtain a global order. It creates a
single throughput ceiling and still cannot prove business causality across external systems. Use
entity-level sequencing, version checks, or a database transaction when the invariant lives there.

---

**Next**: [Consumer Groups, Offsets, and Rebalancing](04_consumer_groups_offsets_and_rebalancing.md)
