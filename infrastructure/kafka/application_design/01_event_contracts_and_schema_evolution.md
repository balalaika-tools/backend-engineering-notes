# Event Contracts Must Outlive Any One Deployment

> **Who this is for**: engineers designing payloads shared by independently deployed services.

## A minimal event with identity and meaning

```json
{
  "event_id": "evt-101",
  "event_type": "order.created",
  "schema_version": 1,
  "occurred_at": "2026-09-04T09:15:00Z",
  "producer": "orders-api",
  "data": {"order_id": "ord-42", "currency": "EUR", "total_minor": 2590}
}
```

Validate this envelope at the producer boundary and again at the consumer boundary. `event_id`
supports deduplication, `event_type` selects behavior, and `schema_version` makes interpretation
explicit. Money uses minor units so binary floating point cannot change the amount.

Save this minimal complete contract as `order-created-v1.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://contracts.example.com/order-created-v1.schema.json",
  "type": "object",
  "required": ["event_id", "event_type", "schema_version", "occurred_at", "producer", "data"],
  "properties": {
    "event_id": {"type": "string", "minLength": 1},
    "event_type": {"const": "order.created"},
    "schema_version": {"const": 1},
    "occurred_at": {"type": "string", "format": "date-time"},
    "producer": {"type": "string", "minLength": 1},
    "data": {
      "type": "object",
      "required": ["order_id", "currency", "total_minor"],
      "properties": {
        "order_id": {"type": "string", "minLength": 1},
        "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
        "total_minor": {"type": "integer", "minimum": 0},
        "coupon_code": {"type": "string"}
      },
      "additionalProperties": true
    }
  },
  "additionalProperties": true
}
```

`additionalProperties` permits an old reader to ignore additive fields. Required fields and their
meaning stay strict. Save the following as `event_contract.py` beside the schema:

```python
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = json.loads(Path(__file__).with_name("order-created-v1.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def validate_event(event: dict) -> None:
    VALIDATOR.validate(event)
```

Run `uv add jsonschema && uv run python -c 'from event_contract import validate_event; validate_event({"event_id":"evt-101","event_type":"order.created","schema_version":1,"occurred_at":"2026-09-04T09:15:00Z","producer":"orders-api","data":{"order_id":"ord-42","currency":"EUR","total_minor":2590}}); print("contract: valid")'`.
The success signal is `contract: valid`; a `jsonschema.exceptions.ValidationError` names the first
rejected path.

---

## 1. Independent deployment makes payload changes distributed changes

An in-process function signature changes atomically with its caller. An event may remain retained
while producers and consumers deploy days apart. Removing `currency` can therefore break replay
long after the producer that wrote the record has gone.

Prefer additive evolution: add optional fields with meaningful defaults, let consumers ignore
unknown fields, and keep the semantic meaning of existing fields stable.

---

## 2. Compatibility is about readers and writers

- **Backward compatibility**: the new reader accepts old data.
- **Forward compatibility**: the old reader accepts new data.
- **Full compatibility**: both directions hold across the supported window.

Changing `total_minor` from integer cents to a decimal major-unit string is not compatible merely
because JSON can represent both. The wire shape and business meaning changed.

Use JSON Schema, Avro, or Protobuf plus a schema registry when automated compatibility enforcement
is worth the platform cost. The registry checks structure; contract tests must still check meaning.

This executable compatibility test makes both directions and a semantic break visible:

```python
from copy import deepcopy

import pytest
from jsonschema import ValidationError

from event_contract import validate_event

V1 = {
    "event_id": "evt-101", "event_type": "order.created", "schema_version": 1,
    "occurred_at": "2026-09-04T09:15:00Z", "producer": "orders-api",
    "data": {"order_id": "ord-42", "currency": "EUR", "total_minor": 2590},
}


def test_additive_field_is_accepted_by_old_and_new_readers() -> None:
    v2_writer = deepcopy(V1)
    v2_writer["data"]["coupon_code"] = "AUTUMN10"
    validate_event(V1)        # new reader still accepts retained v1
    validate_event(v2_writer) # old contract ignores the additive field


def test_semantic_wire_break_is_rejected() -> None:
    broken = deepcopy(V1)
    broken["data"]["total_minor"] = "25.90"  # major-unit string changes shape and meaning
    with pytest.raises(ValidationError, match="not of type 'integer'"):
        validate_event(broken)
```

Run `uv add --dev pytest && uv run pytest -q`. The observable result is `2 passed`; if the second
test does not fail before `pytest.raises` catches it, the validator is not enforcing the money
representation. A schema registry becomes the canonical shared lifecycle when contracts span many
clients; see [Schema Registry and Serialization](05_schema_registry_and_serialization.md).

---

## 3. Events describe facts, not remote commands in disguise

`order.created` states a completed domain fact and can serve many consumers. `send-this-email-now`
targets one worker and behaves more like a command. Mixing the two makes ownership, retries, and
audit meaning unclear.

> **Key insight**: an event contract includes semantics and compatibility promises, not just a
> serializable payload.

---

## 4. What breaks, how to verify, and when not to evolve in place

**Success signal:** compatibility tests read representative retained v1 records with the v2
consumer and exercise v2 records against the oldest supported reader. Checking only schema-registry
acceptance silently misses changed business meaning.

⚠️ Reusing `event_type` while changing its meaning corrupts consumers without a parse error. Publish
a new event type or topic when the fact itself changes.

Do not put large blobs, secrets, or mutable database snapshots into every event. Store blobs in
object storage, publish a stable reference, and minimize personal data whose retention you cannot
later revoke cleanly.

---

**Next**: [Python Producers and Consumers](02_python_producers_and_consumers.md)
