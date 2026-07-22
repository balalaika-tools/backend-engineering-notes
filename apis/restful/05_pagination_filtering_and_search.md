# Pagination, Filtering, and Search

> **Who this is for**: API designers exposing collections that must remain bounded, stable, and operable as data grows. Assumes [Representations, Validation, and Errors](04_representations_validation_and_errors.md).

---

## 1. Every Collection Needs Bounds

An unpaginated collection works until it becomes a memory, latency, database, and egress incident.

```http
GET /orders?limit=50&status=paid&sort=-created_at
```

Define:

- Default and maximum page size
- Stable sort and tie-breaker
- Cursor or offset semantics
- Allowed filters and operators
- Index-supported combinations
- Total-count behavior
- Visibility when data changes between pages
- Maximum query complexity and execution time

The server must enforce limits even if the documentation asks clients to behave.

---

## 2. Offset Pagination

```http
GET /orders?limit=50&offset=100
```

```sql
SELECT id, created_at, status
FROM orders
WHERE tenant_id = :tenant_id
ORDER BY created_at DESC, id DESC
LIMIT :limit OFFSET :offset;
```

Advantages:

- Simple to implement and explain.
- Supports page-number interfaces and arbitrary jumps.

Costs:

- Large offsets can require scanning/discarding many rows.
- Inserts or deletes before the offset cause duplicates or omissions.
- “Page 4” has no durable meaning in a changing collection.

Use offset pagination for small/admin datasets, snapshots, or interfaces where random page access matters more than change stability.

---

## 3. Cursor/Keyset Pagination

A cursor represents the last ordering position:

```sql
SELECT id, created_at, status
FROM orders
WHERE tenant_id = :tenant_id
  AND (created_at, id) < (:cursor_created_at, :cursor_id)
ORDER BY created_at DESC, id DESC
LIMIT :limit_plus_one;
```

The unique `id` tie-breaker prevents two rows with the same timestamp from becoming unstable.

```json
{
  "items": [{"id": "ord_123", "created_at": "2026-07-22T09:30:00Z"}],
  "page": {
    "next_cursor": "eyJ2IjoxLCJjcmVhdGVkX2F0Ijoi...",
    "has_more": true
  }
}
```

Advantages:

- Index-friendly traversal.
- Stable relative to inserts before the current position.
- Natural continuation for feeds.

Costs:

- No cheap arbitrary page jump.
- Cursor must bind to sort/filter context.
- Updates to sort keys can still move items between pages.

> **Rule**: Default to cursor pagination for large or frequently changing operational collections. Use offset when its UX benefit is real and the dataset/query plan is bounded.

---

## 4. Opaque Signed Cursor

A cursor can reveal its fields without allowing clients to modify them. Signing provides integrity, not confidentiality.

```python
import base64
import hashlib
import hmac
import json
from datetime import datetime
from typing import Any


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def encode_cursor(
    *,
    created_at: datetime,
    order_id: str,
    filter_hash: str,
    secret: bytes,
) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "created_at": created_at.isoformat(),
            "order_id": order_id,
            "filter_hash": filter_hash,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def decode_cursor(cursor: str, *, secret: bytes) -> dict[str, Any]:
    try:
        payload_part, signature_part = cursor.split(".", maxsplit=1)
        payload = _b64decode(payload_part)
        supplied_signature = _b64decode(signature_part)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Malformed cursor") from exc

    expected_signature = hmac.new(secret, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError("Invalid cursor signature")

    decoded = json.loads(payload)
    if decoded.get("v") != 1:
        raise ValueError("Unsupported cursor version")
    return decoded
```

In production, rotate signing keys with a key identifier, cap cursor length, validate every decoded field, and reject a cursor whose filter hash does not match the current request.

Do not put sensitive data in a merely encoded cursor. Base64 is readable.

---

## 5. Filtering

Expose an allowlisted query language instead of translating arbitrary field names directly into SQL.

```text
GET /orders?status=paid&created_after=2026-07-01T00:00:00Z&customer_id=cus_7
```

For each filter define:

- Type and normalization
- Equality, range, prefix, or set semantics
- Whether repeated parameters mean AND or OR
- Time-zone and boundary inclusivity
- Authorization implications
- Supported combinations and indexes

Reject unknown filters if silently ignoring them could return a dangerously broader dataset.

```text
❌ requested: ?statsu=cancelled
   ignored typo: returns every order

✅ 400 unknown_filter with allowed filter names
```

Never interpolate filter, sort, or field-selection strings into SQL. Map public names to fixed expressions and bind values as parameters.

---

## 6. Sorting

```text
GET /orders?sort=-created_at,id
```

Use a deterministic final tie-breaker, normally the unique ID. Decide how nulls sort and which collation applies to text. Do not promise a database's accidental row order.

Sort fields are a cost surface. Each supported ordering may require an index combined with common tenant and filter predicates. An API that accepts any field can expose slow scans and data-dependent denial of service.

---

## 7. Search

Use `GET` when the search can be represented safely and compactly in a URI:

```http
GET /orders?q=keyboard&status=paid&limit=20
```

Use `POST` to a search/query resource when the expression is large, structured, or sensitive:

```http
POST /order-searches
Content-Type: application/json

{
  "query": {"all": [{"status": "paid"}, {"total_minor": {"gte": 10000}}]},
  "sort": [{"created_at": "desc"}],
  "limit": 50
}
```

This is still a read operation at the business level, but generic caches and retry tooling treat `POST` differently. Document that it is side-effect-free, cap expression depth/clauses, allowlist fields/operators, and enforce a query deadline.

---

## 8. Counts and Consistency

Exact totals can be far more expensive than fetching one page. Options:

- Omit totals and return only `has_more`.
- Return an estimate and label it.
- Compute an exact total only when explicitly requested and authorized.
- Create an asynchronous report for very expensive counts.

Pages are normally separate database snapshots. If consumers need a stable export, create a snapshot/job and page within it. Do not imply that a cursor freezes the whole collection unless the implementation actually provides snapshot consistency.

---

## 9. Collection Checklist

- Default and maximum limit are enforced.
- Ordering is deterministic and index-supported.
- Cursor includes or binds to filter/sort context.
- Unknown/unsupported filters fail clearly.
- Query fields/operators cannot become raw SQL.
- Count cost and consistency are documented.
- Concurrent insert, delete, and sort-key update tests exist.
- Deep traversal and pathological queries have measured limits.

---

**Next**: [Concurrency, Idempotency, and Retries](06_concurrency_idempotency_and_retries.md)

