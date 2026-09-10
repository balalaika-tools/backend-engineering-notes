# NATS JetStream

> A practical path from one durable message to reliable Python workers and an operable cluster.

[![NATS](https://img.shields.io/badge/NATS-2.14.x-27AAE1.svg?logo=natsdotio&logoColor=white)](https://nats.io/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)

---

## Contents

| Guide | Role | Reader outcome |
|---|---|---|
| [First durable round trip](01_first_durable_round_trip.md) | Foundation/tutorial | Start JetStream, store one event, consume it, and prove replay |
| [Streams, subjects, and retention](02_streams_subjects_and_retention.md) | Foundation | Shape stored data with subject filters, limits, and retention policy |
| [Consumers, acknowledgments, and flow](03_consumers_acknowledgments_and_flow.md) | Deep dive | Derive redelivery and backpressure from consumer state |
| [Async Python services](04_async_python_services.md) | Implementation | Build a bounded `nats-py` publisher and pull worker |
| [Reliable effects and publishing](05_reliable_effects_and_publishing.md) | Deep dive | Place acknowledgments, deduplication, and idempotency at the right boundaries |
| [Operations and recovery](06_operations_and_recovery.md) | Operations | Size, replicate, observe, back up, and recover JetStream |
| [Security and multitenancy](07_security_and_multitenancy.md) | Operations | Restrict clients and isolate tenant resources with accounts and permissions |
| [When to use JetStream](08_when_to_use_jetstream.md) | Decision guide | Choose among Core NATS, JetStream, Kafka, queues, and a database outbox |

---

## Reading Order

### First durable worker

**For**: backend engineers new to NATS.

**Working result by entry 2**: publish `orders.created`, consume and acknowledge it, then explain
which subject selected it and which stream retained it.

1. **Do:** [First durable round trip](01_first_durable_round_trip.md).
2. **Understand:** [Streams, subjects, and retention](02_streams_subjects_and_retention.md).
3. **Understand:** [Consumers, acknowledgments, and flow](03_consumers_acknowledgments_and_flow.md).
4. **Build:** [Async Python services](04_async_python_services.md).

**Stop here if** this is a disposable internal workflow and duplicate effects are harmless.
Continue when a message changes money, inventory, permissions, or another external system.

### Production hardening

**For**: engineers shipping an existing JetStream worker.

**Working result by entry 2**: trace the crash window around one side effect, make it idempotent,
and choose a replication and recovery target.

1. **Do:** [Reliable effects and publishing](05_reliable_effects_and_publishing.md).
2. **Harden:** [Operations and recovery](06_operations_and_recovery.md).
3. **Restrict:** [Security and multitenancy](07_security_and_multitenancy.md).

**Stop here if** redelivery tests pass, storage limits are explicit, recovery is rehearsed, and
alerts cover consumer backlog and unavailable replicas. Continue into cross-domain topologies only
when one cluster or region no longer meets the requirement.

### Architecture decision

**For**: engineers deciding whether JetStream belongs in a design.

**Working result by entry 2**: classify the workload as transient messaging, durable work,
replayable events, or a database-coordinated workflow and reject at least one unsuitable option.

1. **Do:** [When to use JetStream](08_when_to_use_jetstream.md).
2. **Understand:** [Streams, subjects, and retention](02_streams_subjects_and_retention.md).
3. **Revisit for failure semantics:** [Reliable effects and publishing](05_reliable_effects_and_publishing.md).

**Stop here if** Core NATS, a database queue, or an existing broker already satisfies the contract.
Choosing fewer durable systems is often the better operational decision.

---

## Prerequisites

- Comfort with async Python, process crashes, and database transactions.
- [Background work](../../background_work/README.md) helps separate queue delivery from business workflow state.
- [Kafka](../kafka/README.md) provides a retained-log comparison at a larger platform scale.

