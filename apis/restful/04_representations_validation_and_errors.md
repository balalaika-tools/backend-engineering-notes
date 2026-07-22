# Representations, Validation, and Errors

> **Who this is for**: API designers defining payloads that remain precise across languages and failure paths. Assumes [Resource and URI Design](03_resource_and_uri_design.md).

---

## 1. Representations Are Public Types

A representation is a consumer-facing view, not a serialized ORM object.

```json
{
  "id": "ord_123",
  "status": "paid",
  "total": {"amount_minor": 4200, "currency": "EUR"},
  "created_at": "2026-07-22T09:30:00Z"
}
```

This shape makes important decisions explicit:

- Money uses integer minor units rather than binary floating point.
- Currency travels with the amount.
- Time has an offset and a stable text format.
- Status is a documented domain value rather than an internal enum name.
- Internal tenant keys, fraud flags, and database metadata are absent.

Create separate input and output schemas. A response often contains server-managed fields that a consumer must never set.

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class OrderOut(BaseModel):
    id: str
    status: Literal["pending", "paid", "cancelled"]
    amount_minor: int
    currency: str
    created_at: datetime
```

For public input, decide deliberately whether unknown fields are rejected or ignored. Rejecting catches caller mistakes but makes additive transitions harder when traffic passes through mixed-version components. Output consumers should normally tolerate unknown fields.

---

## 2. Media Types and Encoding

`Content-Type` describes the body being sent; `Accept` describes acceptable response types.

Common JSON rules worth stating:

- Object member order has no semantic meaning.
- Missing and explicit `null` are different states when the domain distinguishes “unchanged,” “clear,” and “unknown.”
- JSON numbers do not guarantee every language can represent arbitrary integers or decimals exactly.
- Timestamps should include an offset, normally UTC `Z`, and precision policy.
- Binary content should usually use an appropriate binary media type or object-storage upload rather than large base64 JSON fields.
- Enum-like strings can gain values; consumers need an unknown-value strategy.

If an API accepts JSON Merge Patch or JSON Patch, require its specific media type. `application/json` alone does not define patch semantics.

---

## 3. Validation Is Layered

Validation is not one schema check:

```text
bytes
  → framing and size
  → media type and decoding
  → structural schema
  → field constraints
  → cross-field/domain invariants
  → authentication and authorization
  → current-state/concurrency checks
```

| Layer | Example failure | Typical response |
|-------|-----------------|------------------|
| Framing/decoding | Malformed JSON | `400` |
| Media type | XML sent to JSON-only operation | `415` |
| Structural | Required field absent | `422` or documented `400` policy |
| Field | `amount_minor <= 0` | `422` |
| Cross-field | `end_at < start_at` | `422` |
| Authorization | Caller cannot create for this account | `403` or scoped `404` |
| State | Already cancelled, version stale | `409` or `412` |

Authorization is not validation. A syntactically valid object ID still requires an access decision, ideally inside the scoped query.

Do inexpensive, side-effect-free checks before expensive dependencies. Still enforce critical invariants with database constraints or transactional checks because concurrent requests can pass application validation together.

---

## 4. Problem Details

RFC 9457 defines JSON and XML formats for machine-readable HTTP problem details. JSON uses `application/problem+json`.

```http
HTTP/1.1 409 Conflict
Content-Type: application/problem+json

{
  "type": "https://api.example.com/problems/order-state-conflict",
  "title": "Order state conflict",
  "status": 409,
  "detail": "Only pending orders can be cancelled.",
  "instance": "/orders/ord_123/cancellation",
  "code": "ORDER_NOT_CANCELLABLE",
  "request_id": "req_01J8Q...",
  "current_status": "shipped"
}
```

Standard members:

| Member | Meaning |
|--------|---------|
| `type` | URI identifying the problem class; may resolve to documentation |
| `title` | Short stable human summary for that class |
| `status` | HTTP status for convenience; it does not replace the actual status line |
| `detail` | Occurrence-specific explanation safe for the consumer |
| `instance` | URI reference identifying this occurrence |

Extensions such as stable `code`, `request_id`, field errors, or safe conflict details are allowed. Do not expose stack traces, SQL, internal hosts, raw dependency errors, tokens, or secret-bearing URLs.

> **Rule**: HTTP status is the broad interoperable category; problem `type` or `code` is the stable application decision key; `detail` is for humans.

---

## 5. Complete FastAPI Error Example

```python
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


app = FastAPI()


def problem(
    request: Request,
    *,
    status: int,
    problem_type: str,
    title: str,
    detail: str,
    code: str,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://api.example.com/problems/{problem_type}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
    }
    request_id = getattr(request.state, "request_id", None)
    if request_id is not None:
        body["request_id"] = request_id
    if extensions:
        body.update(extensions)
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


@app.exception_handler(RequestValidationError)
async def validation_problem(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    fields = [
        {
            "path": "/" + "/".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "kind": error["type"],
        }
        for error in exc.errors()
    ]
    return problem(
        request,
        status=422,
        problem_type="validation-error",
        title="Request validation failed",
        detail="One or more request fields are invalid.",
        code="VALIDATION_ERROR",
        extensions={"errors": fields},
    )
```

In a real service, normalize domain exceptions in one place and log the full internal error with the same request ID. The public response and internal evidence serve different audiences.

---

## 6. Batch and Partial Results

Do not hide per-item failures inside an undocumented `200` envelope. Define whether the operation is:

- **Atomic**: all items commit or none; one problem describes the failed request.
- **Independent**: each item has its own stable operation ID and outcome.
- **Asynchronous**: a job resource eventually exposes item outcomes.

```json
{
  "results": [
    {"operation_id": "op_1", "status": "succeeded", "resource_id": "ord_1"},
    {
      "operation_id": "op_2",
      "status": "failed",
      "problem": {
        "type": "https://api.example.com/problems/order-state-conflict",
        "title": "Order state conflict",
        "status": 409,
        "code": "ORDER_NOT_EDITABLE"
      }
    }
  ]
}
```

Document whether the top-level HTTP status represents transport/batch acceptance or aggregated item success. Consumers should not have to guess from one provider-specific convention.

---

## 7. Error Review Checklist

- Request and response models are distinct.
- Units, timestamps, precision, null, missing, and enum evolution are explicit.
- Body size and structural complexity are limited before full processing.
- Domain invariants are repeated transactionally where concurrency matters.
- One stable error format covers framework, domain, gateway, and dependency failures.
- Consumers can distinguish retryable, correctable, forbidden, conflict, and unknown outcomes.
- Public errors are safe; internal evidence is correlated and retained appropriately.

---

## References

- [Problem Details for HTTP APIs — RFC 9457](https://www.rfc-editor.org/rfc/rfc9457)
- [HTTP Semantics — RFC 9110](https://www.rfc-editor.org/rfc/rfc9110)

---

**Next**: [Pagination, Filtering, and Search](05_pagination_filtering_and_search.md)

