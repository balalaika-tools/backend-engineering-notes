# Resource and URI Design

> **Who this is for**: API designers translating domain capabilities into stable resource-oriented HTTP contracts. Assumes [HTTP Semantics](02_http_semantics.md).

---

## 1. Start with Domain Boundaries

Do not expose database tables mechanically. Identify concepts with identity, lifecycle, ownership, and permissions.

```text
database implementation            API resource model
orders table                 ┐
order_items table            ├──> /orders/{order_id}
payment_attempts table       ┘     representation assembled for consumer
```

A resource may be virtual (`/exchange-rates/current`), computed (`/recommendations`), or process-oriented (`/report-runs/{id}`). Conversely, an internal join table may have no reason to appear in the API.

Questions for each candidate resource:

- What stable identity does it have?
- Who owns or may access it?
- What states and transitions are valid?
- Which representation does each consumer need?
- Is it independently addressable?
- What is its retention and deletion policy?

---

## 2. URI Principles

Prefer stable, predictable, opaque identifiers:

```text
/customers
/customers/cus_01J8M...
/orders/ord_01J8N...
/orders/ord_01J8N.../items
```

Useful conventions:

- Use plural nouns for collections.
- Use one casing convention such as kebab-case for multiword path segments.
- Keep implementation names, table names, and file extensions out of URIs.
- Use immutable opaque IDs rather than mutable email addresses or names.
- Treat URIs as identifiers; consumers should not parse business meaning from an ID.
- Keep paths reasonably shallow.

The URI does not need to encode every relationship. Representations and links can express relationships without nesting.

---

## 3. Collections, Members, and Relationships

```text
GET  /projects                         collection
POST /projects                         create member
GET  /projects/{project_id}            member
GET  /projects/{project_id}/members    scoped related collection
GET  /memberships/{membership_id}      relationship as first-class resource
```

Use nesting when the child is naturally scoped by the parent or the parent is required for authorization. Avoid deep chains:

```text
❌ /orgs/{o}/teams/{t}/projects/{p}/tickets/{ticket}/comments/{comment}
✅ /tickets/{ticket}/comments
✅ /comments/{comment}
```

A relationship with its own fields or lifecycle deserves a resource:

```json
{
  "id": "mem_123",
  "organization_id": "org_7",
  "user_id": "usr_9",
  "role": "editor",
  "joined_at": "2026-07-22T09:30:00Z"
}
```

This models membership more accurately than `POST /organizations/{id}/add-user` and gives updates, auditing, and deletion a stable target.

---

## 4. Commands and State Transitions

CRUD is not the whole domain. Model consequential transitions in a way that makes idempotency and resulting state observable.

### Transition as a subresource

```text
PUT /orders/ord_123/cancellation
```

The target represents the cancellation intent/result. Repeating the same `PUT` naturally addresses the same resource.

### Event-like subordinate resource

```text
POST /orders/ord_123/refunds
Idempotency-Key: 8be2...
```

Each refund has identity, amount, status, and failure history, so it should be a resource.

### Explicit command

```text
POST /orders/ord_123:recalculate
```

An explicit action suffix is reasonable when no durable resource model improves the contract. Document authorization, side effects, concurrency, idempotency, and result retrieval. Do not turn every domain operation into an arbitrary verb endpoint without first considering resources.

---

## 5. `PUT` and `PATCH`

`PUT` requests create or replace the state of the target resource. Define whether omitted fields reset to defaults, are server-managed, or make the request invalid.

`PATCH` applies a patch document. The media type defines its semantics:

- `application/merge-patch+json` treats object fields as replacements and `null` as removal, which can conflict with domains where null is meaningful.
- `application/json-patch+json` sends ordered operations such as `add`, `remove`, `replace`, and `test`.
- A custom partial model is common but must explicitly define missing vs null and operation ordering.

```http
PATCH /profiles/usr_123 HTTP/1.1
Content-Type: application/merge-patch+json
If-Match: "profile-8"

{"display_name":"Ada Lovelace"}
```

Use `If-Match` for concurrent editing so one client cannot silently overwrite another. See [Concurrency, Idempotency, and Retries](06_concurrency_idempotency_and_retries.md).

---

## 6. Long-Running Operations

Do not hold a request open for minutes when work naturally becomes a durable job.

```http
POST /report-runs HTTP/1.1
Content-Type: application/json

{"report_type":"monthly_revenue","month":"2026-06"}
```

```http
HTTP/1.1 202 Accepted
Location: /report-runs/run_123

{"id":"run_123","status":"queued"}
```

```text
GET    /report-runs/run_123              inspect progress/outcome
DELETE /report-runs/run_123              request cancellation if supported
GET    /report-runs/run_123/result       retrieve result or redirect to object storage
```

The job representation should define terminal states, timestamps, progress semantics, expiry, cancellation behavior, result link, and structured failure. An idempotency key prevents an uncertain initial response from starting the same expensive job twice.

---

## 7. Bulk Operations

Bulk interfaces reduce round trips but complicate authorization, limits, atomicity, and error reporting.

```json
{
  "operations": [
    {"operation_id": "op_1", "order_id": "ord_1", "status": "shipped"},
    {"operation_id": "op_2", "order_id": "ord_2", "status": "shipped"}
  ]
}
```

Define:

- Maximum item and byte counts
- Whether the batch is atomic or each item commits independently
- Per-item authorization and errors
- Idempotency identity for the batch and/or items
- Ordering and concurrency behavior
- Whether one invalid item rejects the whole request

For large work, create an import/job resource and process asynchronously. Do not allow an unbounded array to become one giant database transaction.

---

## 8. Multi-Tenant Resource Boundaries

Never trust a tenant identifier in the path or body by itself. Derive allowed tenant scope from the authenticated principal and load through that boundary.

```sql
SELECT id, status, total_minor
FROM orders
WHERE tenant_id = :principal_tenant_id
  AND id = :order_id;
```

Whether the URI is `/tenants/{tenant_id}/orders/{id}` or `/orders/{id}`, authorization must prove the relationship. Globally unique IDs improve addressing but do not grant access.

Avoid returning writable internal fields such as `tenant_id`, `is_admin`, fraud scores, or workflow overrides in generic update models. Resource design includes property-level permissions, not only path structure.

---

## 9. Design Review Checklist

- Resources express domain concepts, not tables or controllers.
- IDs are stable and do not leak mutable or sensitive meaning.
- Collection/member relationships and ownership are explicit.
- Methods preserve HTTP safety and idempotency expectations.
- Commands have observable state or documented semantics.
- Long work becomes a job resource.
- `PUT`/`PATCH` missing, null, and concurrency behavior is defined.
- Bulk operations state atomicity, limits, and per-item outcomes.
- Every object and property passes tenant-aware authorization.

---

**Next**: [Representations, Validation, and Errors](04_representations_validation_and_errors.md)

