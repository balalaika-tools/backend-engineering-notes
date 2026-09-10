# Kafka Application Design

> Turn the record-path mental model into stable contracts and safe Python service lifecycles.

---

## Contents

| File | Role | Topic | Reader outcome |
|---|---|---|---|
| [Event contracts](01_event_contracts_and_schema_evolution.md) | Implementation | Payload evolution | Publish contracts old and new consumers can interpret |
| [Python producers and consumers](02_python_producers_and_consumers.md) | Implementation | Client code | Run a complete Python round trip |
| [Processing loops](03_processing_loops_backpressure_and_shutdown.md) | Implementation | Runtime lifecycle | Bound concurrency and shut down without skipping work |
| [Topic and partition design](04_topic_and_partition_design.md) | Decision guide | Topology | Choose names, keys, partitions, retention, and ownership |
| [Schema registry and serialization](05_schema_registry_and_serialization.md) | Implementation | Contract operations | Register, evolve, and restore serialized contracts |

---

## Reading Order

**Working result by entry 2**: validate an `order.created` envelope and publish/consume it from Python.

1. **Do:** define and execute the event-contract validator.
2. **Do:** run the Python producer and consumer with that validator.
3. **Harden:** control processing and topic topology.
4. **Operate:** add a schema registry when independently deployed clients need centralized compatibility enforcement.

**Stop here if** duplicates are harmless and the service is internal. Continue to
[Reliability](../reliability/README.md) when effects must survive crashes safely.
