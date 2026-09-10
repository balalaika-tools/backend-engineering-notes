# A Schema Registry Makes Contract Evolution an Enforced Deployment Gate

> **Who this is for**: teams whose Kafka contracts cross independently deployed applications.

## Register one contract and reject one breaking change

Assume a Schema Registry at `http://localhost:8081` and a subject named
`orders.events.v1-value`. A **subject** is the versioned name under which the registry stores a
schema. **Avro** is a schema-driven binary serialization format. Save this Avro schema as
`order-created-v1.avsc`:

```json
{
  "type": "record",
  "name": "OrderCreated",
  "namespace": "com.example.orders",
  "fields": [
    {"name": "event_id", "type": "string"},
    {"name": "order_id", "type": "string"},
    {"name": "total_minor", "type": "long"}
  ]
}
```

Set the subject to backward-transitive compatibility, then register v1:

```bash
curl --fail-with-body -X PUT http://localhost:8081/config/orders.events.v1-value \
  -H 'Content-Type: application/vnd.schemaregistry.v1+json' \
  -d '{"compatibility":"BACKWARD_TRANSITIVE"}'

jq -Rs '{schema: .}' order-created-v1.avsc | \
  curl --fail-with-body -X POST \
    http://localhost:8081/subjects/orders.events.v1-value/versions \
    -H 'Content-Type: application/vnd.schemaregistry.v1+json' \
    --data-binary @-
```

The success signal is a response containing an integer schema `id`. An HTTP `409` means the new
schema violates the subject's compatibility policy; a `200` from the compatibility endpoint or an
ID from registration is the gate, not a local parse alone. The complete endpoint behavior is in
the [Schema Registry API reference](https://docs.confluent.io/platform/current/schema-registry/develop/api.html).

---

## 1. A shared ID prevents consumers from guessing the writer schema

Plain JSON bytes do not identify which contract produced them. During a rolling deployment, a
consumer can parse valid JSON while interpreting the wrong generation. Registry-aware serializers
write a schema identifier with the record; deserializers fetch that exact writer schema and resolve
it against their reader schema.

The registry ID and the subject version answer different questions: the ID identifies schema
content on the wire, while the subject version records that contract's evolution. Do not embed the
subject version as if it were the wire ID.

> **Core:** configure serializers for the same subject-naming strategy in every language, and keep
> automatic registration disabled in production unless producer identities are explicitly allowed
> to evolve contracts.

---

## 2. Compatibility checks structural evolution before deployment

Adding an optional field with a default lets a new reader consume retained v1 records:

```bash
jq '.fields += [{"name":"coupon_code","type":["null","string"],"default":null}]' \
  order-created-v1.avsc > order-created-v2.avsc
```

The generated field is:

```json
{"name": "coupon_code", "type": ["null", "string"], "default": null}
```

Before registration, submit the candidate against all supported versions:

```bash
jq -Rs '{schema: .}' order-created-v2.avsc | \
  curl --fail-with-body -X POST \
    'http://localhost:8081/compatibility/subjects/orders.events.v1-value/versions?verbose=true' \
    -H 'Content-Type: application/vnd.schemaregistry.v1+json' \
    --data-binary @-
```

The additive schema returns `{"is_compatible":true}`. Create a breaking candidate by changing the
existing field type, then send it to the same compatibility endpoint:

```bash
jq '(.fields[] | select(.name == "total_minor").type) = "string"' \
  order-created-v1.avsc > order-created-breaking.avsc
```

That candidate returns `{"is_compatible":false}` with diagnostics. The registry protects wire
structure; the semantic test in [Event Contracts](01_event_contracts_and_schema_evolution.md) must
still prove that “minor units” did not silently become major units.

> **Key insight**: a registry can prove that readers can decode bytes; only contract tests can
> prove that independently deployed services assign the same meaning to those bytes.

---

## 3. Registry state belongs in backup and recovery plans

Export subjects, versions, compatibility settings, references, and ID mappings with the Kafka data
they describe. Restore them before consumers resume. Re-registering schemas in a fresh registry can
assign different IDs, so copied records may point at the wrong or missing schema even though every
topic exists.

**Success signal:** a restore drill deserializes a retained record written with both the oldest and
newest supported schema IDs. If topic replay fails with “schema not found,” the data backup and
registry backup were not one recovery unit.

> **Production:** restrict schema registration and deletion, monitor compatibility failures and
> lookup latency, cache schemas in clients with bounded refresh, and test the registry-unavailable
> behavior before shipping.

---

## 4. What breaks, and when not to add a registry

⚠️ Deleting a schema version that retained records still reference makes otherwise healthy Kafka
data undecodable. Prefer soft deletion, retention-aware review, and a restore test before permanent
deletion.

Do not add a network registry to a single-process, short-lived stream when schemas never cross a
deployment boundary and local validation already owns the contract. The additional service earns
its cost when centralized compatibility, many languages, or retained historical schemas matter.

---

**Next**: [Kafka Reliability](../reliability/README.md)
