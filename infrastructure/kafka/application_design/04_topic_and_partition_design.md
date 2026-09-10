# Topic Topology Is an Application Contract, Not Folder Organization

> **Who this is for**: engineers deciding topic boundaries, keys, partitions, and retention.

## Start from invariants, not naming aesthetics

For order lifecycle facts, a practical starting decision is one `orders.events.v1` topic keyed by
`order_id`, retained long enough for supported replay, with partitions sized for expected peak
consumer parallelism. Split it only when access, retention, ownership, or throughput requirements
actually differ.

---

## 1. A topic boundary changes operational policy

Events in one topic share major policy surfaces: authorization, retention/compaction, partition
count, quotas, and consumer discovery. Separate payments from public catalog events when access
control differs; do not create one topic per event type by reflex. The operational owner for
creation, effective overrides, quotas, and rollback is
[Configuration and Topic Administration](../operations/05_configuration_and_topic_administration.md).

---

## 2. Partition count sets parallelism and ongoing cost

More partitions permit more conventional consumers, but also increase metadata, open files,
replication traffic, rebalances, and recovery work. Size from peak bytes/second and handler capacity,
then include measured headroom. Increasing later can change key placement; decreasing requires a
new topic.

---

## 3. Ownership prevents incompatible producers

Assign a team to the topic and schema, restrict write ACLs, document the key and compatibility
policy, and disable uncontrolled automatic topic creation. A topic that “everyone can publish to”
eventually has no enforceable meaning.

| Decision | Default starting point | Change when |
|---|---|---|
| Key | Stable aggregate/entity ID | Ordering or skew requires another invariant |
| Cleanup | Time retention | Latest value per key is the intended model |
| Partitions | Measured peak plus headroom | Consumers or throughput exceed measured capacity |
| Naming | Domain + event family + contract generation | Platform convention mandates another form |

**Success signal:** a design review can state the owner, writer ACL, key invariant, replay window,
peak estimate, and migration plan. A topic name alone is not a design.

> **Key insight**: topic design packages application semantics with shared operational policy, so
> splitting or merging topics changes more than discoverability.

---

## 4. What breaks, and when not to share a topic

⚠️ Mixed sensitivity in one topic forces broad readers to receive restricted fields. Consumer-side
filtering is not authorization because the data has already crossed the boundary.

Do not share a topic when producers cannot agree on ownership, key semantics, compatibility, or
retention. Separate topics and integrate deliberately.

---

**Next**: [Schema Registry and Serialization](05_schema_registry_and_serialization.md)
