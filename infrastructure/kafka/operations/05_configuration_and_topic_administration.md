# Kafka Configuration Changes Need Evidence and a Rollback Value

> **Who this is for**: engineers creating topics or changing Kafka policy in a shared cluster.

## Create one explicit topic and inspect the effective result

With authenticated admin settings in `admin.properties`, create the application-owned topic. The
same reviewed state should include its access-control lists (ACLs) and quotas:

```bash
kafka-topics.sh --bootstrap-server broker-1.example.com:9093 \
  --command-config admin.properties --create \
  --topic orders.events.v1 --partitions 15 --replication-factor 3 \
  --config cleanup.policy=delete --config retention.ms=604800000 \
  --config min.insync.replicas=2

kafka-configs.sh --bootstrap-server broker-1.example.com:9093 \
  --command-config admin.properties --entity-type topics \
  --entity-name orders.events.v1 --describe
```

The success signal is `orders.events.v1` with 15 partitions and explicit overrides for seven-day
retention and two in-sync replicas. “Topic already exists” is not success: describe and compare the
effective values because auto-creation may have installed unsafe defaults. Apache Kafka's
[basic operations guide](https://kafka.apache.org/43/operations/basic-kafka-operations/) owns the
current CLI forms.

---

## 1. The nearest explicit override wins

A topic uses its per-topic override when present; otherwise it inherits the broker default. Client
configuration is a separate boundary: application or framework values can override client-library
defaults but cannot rewrite a topic's retention policy.

```text
broker default retention.ms=2592000000 (30 days)
topic override retention.ms=604800000   (7 days)
effective orders.events.v1 retention    = 7 days
delete topic override
effective orders.events.v1 retention    = 30 days
```

Record the old explicit value *and* the inherited fallback before alteration. Removing an override
is a rollback only when the fallback is known. The [topic configuration reference](https://kafka.apache.org/43/configuration/topic-configs/)
lists each property and its server default.

> **Key insight**: a configuration change is safe only when the team records both the value being
> changed and the value that becomes effective after rollback.

---

## 2. Roll out mutable settings as observed state changes

For a seven-day to fourteen-day retention change:

```bash
kafka-configs.sh --bootstrap-server broker-1.example.com:9093 \
  --command-config admin.properties --entity-type topics \
  --entity-name orders.events.v1 --alter \
  --add-config retention.ms=1209600000
```

Describe the topic, publish a canary, and watch disk growth and delete-segment behavior for the
agreed observation window. Roll back to the recorded seven-day override with the same command and
`retention.ms=604800000`; do not use `--delete-config retention.ms` unless inheriting the broker
default is the intended result.

Kafka classifies broker properties by dynamic update mode: read-only settings require restart,
while per-broker or cluster-wide settings can change dynamically. Verify the mode in the
[broker configuration reference](https://kafka.apache.org/43/configuration/broker-configs/) before
planning the rollout.

---

## 3. Partition, reassignment, quota, and deletion operations are not symmetric

- Partition count can increase but cannot decrease in place; expansion can change keyed placement.
- Replica reassignment moves data and needs a throttle high enough that replica lag keeps falling.
- User/client quotas limit producer bytes, consumer bytes, or request time and should start with a
  canary identity before a tenant-wide default.
- Topic deletion removes the namespace and eventually its data; recovery depends on backups and
  retention, not an undo command.

> **Production:** store desired topics, ACLs, quotas, and overrides in reviewed configuration, deny
> uncontrolled auto-creation, and reconcile drift. Keep emergency CLI access audited, not routine.

**Success signal:** the change record contains before/after `--describe` output, canary outcome,
metric observation, owner, and exact rollback command. A completed CLI response with growing
under-replicated partitions is a failed rollout.

---

## 4. What breaks, and when not to alter in place

⚠️ A reassignment throttle below incoming write throughput may never catch up. Verify the plan until
completion and remove the temporary throttle; forgotten throttles silently slow later recovery.

⚠️ Deleting or manually changing Kafka internal topics can corrupt coordinator or metadata state.
Operate only application-owned topics through this workflow.

Do not alter in place when the change modifies key placement, record meaning, or cleanup semantics
in a way old consumers cannot tolerate. Create a new topic, dual-publish or backfill deliberately,
move consumers, and retire the old topic after the rollback window.

---

**Next**: [Ecosystem and Decisions](../ecosystem/README.md)
