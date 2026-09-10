# Kafka Incidents Need Broker and Business Signals Together

> **Who this is for**: teams defining dashboards, alerts, and first-response checks.

## Lag alone cannot identify the bottleneck

Rising lag may mean slow handlers, partition skew, rebalances, broker latency, or a failed downstream
database. Correlate consumer lag and record age with processing latency, error rate, assignment
changes, broker request latency, ISR shrinkage, controller health, and disk utilization.

---

## 1. Triage from consequence toward cause

```text
business freshness breached?
  → which group/topic/partition?
  → arrival spike or processing slowdown?
  → rebalance churn or dependency errors?
  → broker latency, ISR, disk, controller quorum?
```

Alert on sustained user-impacting conditions, not every transient metric movement. Preserve client
IDs, group IDs, topic, partition, offset, and event ID in structured diagnostics without logging
credentials or sensitive payloads.

Assume the exporter exposes `kafka_consumergroup_lag`, application metric
`orders_event_age_seconds`, JMX metric
`kafka_server_replicamanager_underreplicatedpartitions`, and node filesystem metrics. Record or
rename vendor-specific metrics into these stable names, then make the thresholds executable:

```yaml
groups:
  - name: kafka-orders
    rules:
      - alert: KafkaOrdersFreshnessBreached
        expr: |
          max by (cluster, consumergroup, topic, partition) (
            kafka_consumergroup_lag{consumergroup="billing-v1",topic="orders.events.v1"}
          ) > 1000
          and on (cluster, consumergroup, topic, partition)
          max by (cluster, consumergroup, topic, partition) (
            orders_event_age_seconds{consumergroup="billing-v1",topic="orders.events.v1"}
          ) > 120
        for: 10m
        labels: {severity: page, service: billing}
        annotations:
          summary: "billing-v1 is more than 2 minutes stale"

      - alert: KafkaReplicaOrDiskRisk
        expr: |
          max by (cluster) (kafka_server_replicamanager_underreplicatedpartitions) > 0
          or on (cluster)
          (1 - min by (cluster) (
            node_filesystem_avail_bytes{mountpoint="/var/lib/kafka"}
            / node_filesystem_size_bytes{mountpoint="/var/lib/kafka"}
          )) > 0.85
        for: 15m
        labels: {severity: page, service: kafka}
        annotations:
          summary: "Kafka replicas are under-replicated or data disk exceeds 85%"
```

The first alert requires both backlog and old records, avoiding pages for a large but fast-moving
batch. Its firing labels identify `cluster`, `consumergroup`, `topic`, and `partition`; the first
runbook action is to compare that partition's arrival rate and handler latency, then inspect recent
group assignments. A missing `orders_event_age_seconds` series is not green health—alert separately
on `absent()` because a stopped exporter otherwise suppresses the joined alert.

The second alert identifies the cluster. First check which broker holds the affected replicas and
whether `/var/lib/kafka` is filling; stop reassignment or expansion before disk exhaustion. A broker
restart can transiently under-replicate partitions, which is why the alert waits 15 minutes; do not
raise the duration until it hides an inability to recover.

**Success signal:** an injected slow handler produces an alert that identifies the group and hot
partition, and the runbook distinguishes scaling from replay or broker repair. A green cluster
dashboard silently misses stopped consumers.

> **Key insight**: Kafka health is the ability of a named event flow to stay fresh and recover, not
the mere availability of broker processes.

---

## 2. What breaks, and when not to page

⚠️ Topic-wide average lag hides one stuck partition behind many idle partitions.

Do not page on a momentary rebalance or small lag without a breached duration or freshness objective.
Use tickets or dashboards for capacity trends; reserve pages for actionable urgency.

---

**Next**: [Deployment, Upgrades, and Disaster Recovery](04_deployment_upgrades_and_disaster_recovery.md)
