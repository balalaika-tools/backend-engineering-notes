# Kafka Connect Owns Repeated Data Movement, Not Business Workflows

> **Who this is for**: engineers moving data between Kafka and databases, object stores, or search.

## Choose Connect when the job is mostly translation and checkpointing

A source connector reads an external system into Kafka; a sink connector writes Kafka records out.
Connect workers manage tasks, offsets, scaling, and restart. Use a proven connector for standard
**change data capture (CDC)**—reading committed changes from a database log rather than repeatedly
polling tables—or warehouse delivery; write application code when domain decisions and external
orchestration dominate.

---

## 1. Connector configuration is executable trust

Plugins run code inside workers and the REST API can create workloads. Isolate trusted plugins,
protect the API, restrict client-config overrides, and version configuration. A connector being
“configuration only” does not make it low risk.

**Success signal:** stop and restart a task; it resumes from its checkpoint without missing data,
and an unauthorized principal cannot alter connectors. Healthy worker processes alone can hide a
failed task.

Connect serializers and converters depend on the same contract lifecycle as applications. See
[Schema Registry and Serialization](../application_design/05_schema_registry_and_serialization.md)
for compatibility gates and restore requirements.

> **Key insight**: Connect standardizes operational mechanics around data movement; it does not
> remove the need to understand source consistency, sink idempotency, or schema evolution.

---

## 2. What breaks, and when not to use Connect

⚠️ A sink that is not idempotent can duplicate rows when a task retries after writing but before
checkpointing.

Do not bury a multi-step business workflow in transforms and connector callbacks. Use an owned
service or workflow engine with explicit domain state.

---

**Next**: [Stream Processing](02_stream_processing.md)
