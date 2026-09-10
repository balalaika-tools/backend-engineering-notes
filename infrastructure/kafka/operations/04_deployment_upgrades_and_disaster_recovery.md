# Recovery Requires a Tested Ownership and Data-Loss Contract

> **Who this is for**: teams choosing managed Kafka or owning cluster lifecycle.

## Decide recovery objectives before choosing topology

State the recovery-time objective (RTO), recovery-point objective (RPO), regional failure model,
and who can declare failover. Replication inside one cluster does not provide independent regional
recovery.

---

## 1. Managed service changes responsibility, not semantics

A provider may own broker replacement, patching, and control-plane durability. Your team still owns
topics, keys, schemas, ACLs, quotas, client compatibility, lag, replay, and downstream idempotency.

Kafka 4.x uses KRaft; ZooKeeper belongs only in migration plans for legacy 3.x clusters. Rolling
upgrades require checking supported version paths, protocol compatibility, client versions, and
feature gates against the exact release notes.

---

## 2. Cross-cluster replication is not automatic failover correctness

MirrorMaker or provider replication copies records asynchronously. Consumer offsets, topic configs,
ACLs, schemas, and external effects need explicit recovery treatment. Failback can duplicate or
reorder business processing.

Suppose `eu-primary` replicates to `eu-replica`, billing checkpoints are copied every 30 seconds,
schemas are exported with the same subject/version IDs, and the payment provider deduplicates by
`event_id`:

```text
10:00:00 primary orders offset=1200; replica offset=1198; copied group offset=1195
10:00:05 primary region is lost
10:00:20 operator freezes producers and declares failover
10:00:35 schema/ACL/config checks pass on replica; consumers resume at copied offset=1195
          offsets 1195..1198 may run again; 1199..1200 are absent
10:01:10 producers switch to replica; first accepted write proves recovery
```

The measured recovery-point objective (RPO) is two missing records—offsets 1199 and 1200—not the
30-second checkpoint interval. The recovery-time objective (RTO) is 65 seconds from loss to the
first accepted write. Replaying 1195–1198 is expected; downstream idempotency must collapse those
duplicates.

On failback, stop writes, replicate the replica's new tail to the repaired primary, verify schemas
and offsets again, then move producers before consumers. Running both sides writable creates two
histories that no offset translation can safely order; events produced near either cutover may be
duplicated or observed in a different cross-partition order.

**Success signal:** a game day loses a broker or region, restores the declared event flows within
RTO, measures actual RPO, and proves idempotent resume. A replicated topic count alone is
insufficient. Use the process and environment boundaries in
[Testing Kafka Services](../reliability/05_testing_kafka_services.md) to keep the exercise observable.

> **Key insight**: disaster recovery is a coordinated application-state transition; copied Kafka
> records are only one input to it.

---

## 3. What breaks, and when not to self-host

⚠️ An untested restore often discovers missing schemas, ACLs, offsets, or credentials after the data
has already been copied. Restore and verify the
[schema registry](../application_design/05_schema_registry_and_serialization.md) before resuming consumers.

Do not self-host when no team owns 24/7 storage, quorum, upgrade, certificate, and restore duties.

---

**Next**: [Configuration and Topic Administration](05_configuration_and_topic_administration.md)
