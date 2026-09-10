# Consumer Groups Trade Partition Ownership for Parallel Work

> **Who this is for**: engineers scaling consumers or diagnosing duplicate work and lag spikes.

## A fourth consumer does not make three partitions faster

With three partitions and four consumers in group `billing-v1`, three consumers own one partition
each and one is idle. A conventional consumer-group partition has one active owner at a time:

```text
orders P0 → billing-1
orders P1 → billing-2
orders P2 → billing-3
             billing-4 (idle)
```

---

## 1. Group identity creates an independent subscription

Consumers sharing a `group.id` divide partitions. A different group reads the same retained records
from its own positions. Use `billing-v1` and `fraud-v1` for independent applications; do not give
unrelated services the same group merely because they consume the same topic.

The group's committed offset is a recovery checkpoint, usually the next record to read. It is not
the consumer's live in-memory position and can lag behind work already fetched.

---

## 2. Rebalancing moves ownership and exposes unsafe processing

Membership or subscription changes cause partition assignment to change. A slow consumer can lose
ownership while still processing a record. If it performs an external effect and fails before its
offset is safely committed, the new owner processes that record again.

```text
C1 reads offset 8 → charges card → rebalance/crash → no commit
C2 owns partition → reads offset 8 → charge attempted again
```

This is why consumer effects need idempotency; “one owner at a time” is not “one execution ever.”

---

## 3. Polling is both data access and membership health

The consumer must poll often enough to remain healthy. Long record processing can exceed the
allowed poll interval, trigger reassignment, and amplify duplicates. Bound work per poll, pause
partitions while capacity is full, or separate polling from bounded workers without committing
past unfinished records.

The **consumer group protocol** is Kafka's newer broker-driven assignment protocol, selected by the
client's effective `group.protocol=consumer`; the classic protocol keeps more coordination policy
in clients. It reduces client-side coordination work, but it does not remove the application
requirement to finish or revoke partition work safely. Treat the effective client and broker
settings as an operational configuration boundary; [Kafka configuration and administration](../operations/05_configuration_and_topic_administration.md)
owns the rollout check.

---

## 4. The observable signals tell different stories

- Rising lag with stable membership means processing capacity is below arrival rate.
- Repeated assignment changes indicate churn or poll stalls.
- Many duplicates around rebalances indicate non-idempotent effects or unsafe commits.
- Idle consumers with lag mean skew, blocked processing, or more consumers than partitions.

**Success signal:** an assignment inspection accounts for every partition exactly once within the
group, and a controlled consumer restart causes a bounded handoff without lost effects.

> **Key insight**: a consumer group coordinates partition ownership; correctness still depends on
> how application effects and offset checkpoints cross crashes.

---

## 5. What breaks, and when not to use conventional groups

⚠️ Auto-committing offsets can advance the checkpoint before slow business processing completes.
The symptom after a crash is missing effects even though consumer lag looked healthy.

Do not use conventional groups when many workers must concurrently process individual records from
the same partition with per-record acknowledgment. Evaluate [share groups](../ecosystem/03_share_groups_and_queue_semantics.md)
or a purpose-built work queue.

---

**Next**: [Replication, Leaders, and KRaft](05_replication_leaders_and_kraft.md)
