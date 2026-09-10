# Apache Kafka

> A practical course in designing, building, and operating Kafka-backed Python systems.

[![Apache Kafka](https://img.shields.io/badge/Apache_Kafka-4.3.x-231F20.svg?logo=apachekafka&logoColor=white)](https://kafka.apache.org/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)

---

## Structure

```text
kafka/
│
│ ── LEARN THE RECORD PATH ──────────────────────────────
├── fundamentals/          First event, logs, partitions, groups, replication
│
│ ── BUILD APPLICATIONS ─────────────────────────────────
├── application_design/    Contracts, Python clients, processing loops, topic design
│
│ ── SURVIVE FAILURE ────────────────────────────────────
├── reliability/           Delivery semantics, transactions, retries, outbox
│
│ ── RUN THE PLATFORM ───────────────────────────────────
├── operations/            Security, capacity, observability, upgrades, recovery
│
│ ── EXTEND OR REPLACE IT ───────────────────────────────
└── ecosystem/             Connect, stream processing, share groups, alternatives
```

---

## Contents

| Section | Reader outcome |
|---|---|
| [Fundamentals](fundamentals/README.md) | Run Kafka and trace a record through storage, replication, and consumption |
| [Application design](application_design/README.md) | Build Python producers and consumers around evolvable contracts |
| [Reliability](reliability/README.md) | Choose delivery guarantees and recover without corrupting side effects |
| [Operations](operations/README.md) | Secure, size, observe, upgrade, and recover a cluster |
| [Ecosystem and decisions](ecosystem/README.md) | Use Connect, processing, or share groups only when their problem appears |

---

## Reading Order

### Explore Kafka with a runnable record

**For**: backend engineers who have not operated Kafka before.

**Working result by entry 2**: publish and consume `order.created`, then explain its topic,
partition, offset, and retention independently of the consumer.

1. **Do:** [First event round trip](fundamentals/01_first_event_round_trip.md).
2. **Understand:** [Logs, topics, partitions, and offsets](fundamentals/02_log_topics_partitions_and_offsets.md).
3. **Understand:** [Keys and ordering](fundamentals/03_partitioning_keys_and_ordering.md), then [consumer groups](fundamentals/04_consumer_groups_offsets_and_rebalancing.md).
4. **Build:** [Python producers and consumers](application_design/02_python_producers_and_consumers.md).
5. **Revisit for service design:** [Event contracts](application_design/01_event_contracts_and_schema_evolution.md) and [topics](application_design/04_topic_and_partition_design.md).

This is an exploration route: it gets a record moving before introducing contract governance. If
you are building a service rather than exploring Kafka, follow the
[contract-first application path](application_design/README.md) so validation exists before client
code publishes the payload.

**Stop here if** an internal event flow may tolerate occasional duplicate processing. Continue to
[Reliability](reliability/README.md) when processing changes money, inventory, permissions, or
another external system.

### Production hardening

**For**: engineers taking an existing event flow to production.

**Working result by entry 2**: derive its real delivery guarantee from a crash trace and select an
idempotency boundary.

1. **Do:** [Trace delivery semantics](reliability/01_delivery_semantics.md).
2. **Build:** implement the transaction trace in [Idempotence and transactions](reliability/02_idempotence_transactions_and_exactly_once.md).
3. **Test:** exercise crash windows with the [Kafka service test harness](reliability/05_testing_kafka_services.md).
4. **Recover:** [Retries, dead letters, and replay](reliability/03_retries_dead_letters_and_replay.md).
5. **Operate:** [Security](operations/01_security_and_multitenancy.md), [capacity](operations/02_capacity_planning_and_performance.md), and [observability](operations/03_observability_and_incident_response.md).

**Stop here if** the service has bounded lag, idempotent effects, tested replay, and actionable
alerts. Continue to deployment and disaster recovery when your team owns the cluster lifecycle.

### Architecture decision

**For**: engineers deciding whether Kafka belongs in a design.

**Working result by entry 2**: classify the workload as an event log, work queue, stream processor,
or direct integration and reject at least one unsuitable option.

1. **Do:** [When to use Kafka](ecosystem/04_when_to_use_kafka.md).
2. **Understand:** [The retained-log model](fundamentals/02_log_topics_partitions_and_offsets.md).
3. **Branch:** [Share groups](ecosystem/03_share_groups_and_queue_semantics.md), [Connect](ecosystem/01_kafka_connect_and_data_integration.md), or [stream processing](ecosystem/02_stream_processing.md) only when needed.

**Stop here if** a simpler queue, database table, webhook, or direct call satisfies the delivery
contract. Kafka is valuable only when its retained, partitioned log is part of the requirement.

---

## Prerequisites

- Comfort with processes, network failures, and database transactions.
- [Background work](../../background_work/README.md) is useful when comparing events with durable jobs.
- [Redis Streams](../redis/02_pubsub_and_streams.md) provides a smaller-system comparison.
