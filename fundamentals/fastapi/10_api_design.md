# 10 - API Design Conventions

> **Purpose**: Design HTTP APIs that are predictable for clients, boring to operate, and easy to evolve without breaking consumers.

FastAPI makes it easy to expose routes. API design is the discipline of making those routes behave like a coherent product contract.

The contract has three layers:

1. **Resource model**: what nouns exist, how they relate, and who owns them.
2. **HTTP semantics**: which method means read, create, replace, patch, delete, or retry safely.
3. **Evolution rules**: how clients paginate, handle errors, survive new fields, and migrate across versions.

---

## Resource Naming

Use plural nouns for collections and IDs for individual resources.

```text
GET    /users
POST   /users
GET    /users/{user_id}
PATCH  /users/{user_id}
DELETE /users/{user_id}
```

Nested resources are useful when the child has no meaning without the parent:

```text
GET  /users/{user_id}/sessions
POST /users/{user_id}/sessions
```

Avoid deep nesting:

```text
# Hard to read and hard to authorize
/orgs/{org_id}/teams/{team_id}/projects/{project_id}/tickets/{ticket_id}/comments

# Usually enough
/tickets/{ticket_id}/comments
```

If a child resource has a globally unique ID, let clients address it directly.

```text
GET /comments/{comment_id}
```

Use verbs only for operations that are not clean CRUD resources:

```text
POST /invoices/{invoice_id}/void
POST /reports/{report_id}/rerun
POST /exports
```

Even then, ask whether the operation is really creating a resource:

```text
# Better than POST /reports/generate
POST /report-runs
GET  /report-runs/{run_id}
```

---

## Method Semantics

HTTP already gives you a vocabulary. Use it.

| Method | Use for | Body? | Idempotent? |
|--------|---------|-------|-------------|
| `GET` | Read a resource or collection | No | Yes |
| `POST` | Create a subordinate resource or start an operation | Yes | No, unless you add an idempotency key |
| `PUT` | Replace a resource at a known URI | Yes | Yes |
| `PATCH` | Partially update a resource | Yes | Usually no; can be designed as idempotent |
| `DELETE` | Delete a resource | Usually no | Yes |

Idempotent means "same intended effect if repeated." `DELETE /users/123` can return `204` once and `404` later, but the intended server state is the same: user 123 is gone.

Do not put unsafe actions behind `GET`:

```text
# Wrong: crawlers, previews, and caches can trigger this
GET /emails/{id}?action=send

# Correct
POST /emails/{id}/send
```

For retryable `POST` operations that create money movement, orders, or expensive LLM calls, require an idempotency key. See [Safe API Calls / 11 - Idempotency Keys](safe_and_scalable_api_calls/11_idempotency.md).

---

## Status Codes

Prefer a small, consistent subset.

| Code | Meaning |
|------|---------|
| `200 OK` | Successful read or update with a body |
| `201 Created` | Resource created; include `Location` when useful |
| `202 Accepted` | Work accepted but not complete |
| `204 No Content` | Success with no response body |
| `400 Bad Request` | Invalid syntax or unsupported parameters |
| `401 Unauthorized` | Missing or invalid credentials |
| `403 Forbidden` | Authenticated, but not allowed |
| `404 Not Found` | Resource does not exist or is intentionally hidden |
| `409 Conflict` | State conflict, duplicate, stale version |
| `412 Precondition Failed` | Failed `If-Match` / optimistic concurrency check |
| `422 Unprocessable Content` | Valid JSON, invalid domain data |
| `429 Too Many Requests` | Rate or quota limit |
| `500 Internal Server Error` | Bug or unexpected server failure |
| `502 Bad Gateway` | Upstream returned invalid/error response |
| `503 Service Unavailable` | Temporary overload or dependency outage |
| `504 Gateway Timeout` | Upstream timed out |

Do not encode errors as `200 OK`:

```json
{"status": "error", "message": "not found"}
```

Clients, proxies, metrics, retries, and alerts all understand status codes. Make them do useful work.

---

## Error Shape

Pick one error envelope and use it everywhere. This repo's error guide uses a structured response with a stable `code`, human `message`, and optional field details. That shape is compatible with the idea behind RFC 9457 Problem Details: machine-readable error details without inventing a new one-off format per endpoint.

```json
{
  "code": "VALIDATION_ERROR",
  "message": "Request validation failed",
  "details": [
    {"field": "email", "message": "Enter a valid email address"}
  ],
  "request_id": "req_abc123"
}
```

Rules:

- `code` is for programs. Keep it stable.
- `message` is for humans. It can improve over time.
- `details` is for field-level or item-level errors.
- `request_id` lets support find the server-side log.
- Never expose stack traces, SQL, secrets, token values, or internal hostnames.

See [07 - Error Responses](07_error_handling.md) for implementation.

---

## Pagination

Every collection endpoint needs a pagination story before it reaches production.

### Default to cursor pagination

Cursor pagination is stable under inserts and deletes because the cursor points at a position in a deterministic ordering.

```text
GET /tickets?limit=50
GET /tickets?limit=50&cursor=eyJjcmVhdGVkX2F0IjoiMjAyNi0wNy0wMVQ...
```

Response:

```json
{
  "items": [
    {"id": "t_123", "created_at": "2026-07-01T10:00:00Z"}
  ],
  "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNi0wNy0wMVQ...",
  "has_more": true
}
```

Implementation pattern:

```python
from typing import Generic, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")


class PageParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None


class CursorPage(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None
    has_more: bool
```

Use a deterministic sort with a tie-breaker:

```sql
ORDER BY created_at DESC, id DESC
LIMIT :limit_plus_one
```

The cursor should be opaque. Clients should pass it back, not inspect it.

### Use offset pagination only when it fits

Offset pagination is fine for small, admin, or mostly-static lists:

```text
GET /audit-log?limit=50&offset=100
```

It is a poor default for high-write tables because items can be skipped or duplicated when new rows arrive between page requests.

### Link headers

For public APIs and generic HTTP clients, consider also returning RFC 8288 `Link` headers:

```text
Link: </tickets?limit=50&cursor=abc>; rel="next"
```

The JSON body is easier for frontend apps. The header is friendlier to API tooling. You can provide both.

---

## Filtering and Sorting

Use query parameters for collection modifiers.

```text
GET /tickets?status=open&assignee_id=u_123&sort=-created_at
```

Rules:

- Allowlist filter fields. Never pass arbitrary query keys into SQL.
- Allowlist sort fields. Never let clients sort by raw column names.
- Use a stable secondary sort (`id`) for paginated endpoints.
- Decide whether filters are exact match, prefix, full text, or range. Name range filters explicitly.

```text
GET /tickets?created_after=2026-07-01T00:00:00Z
GET /tickets?created_before=2026-07-02T00:00:00Z
```

Avoid overloading one parameter with a mini-language unless you are intentionally building a query API.

---

## Versioning

Do not version because it feels tidy. Version when you need to make a breaking change.

Good breaking-change candidates:

- Removing a field.
- Renaming a field.
- Changing a field's type.
- Changing enum meaning.
- Changing auth semantics.
- Changing pagination format.
- Changing error shape.

Usually non-breaking:

- Adding an optional response field.
- Adding a new endpoint.
- Adding a new enum value only if clients are documented to tolerate unknown values.
- Adding a new optional request field.

For most backend APIs, URL major versions are the least surprising:

```text
/v1/tickets
/v2/tickets
```

Keep minor evolution additive inside the same version. Announce deprecations with concrete dates and telemetry: which clients still call the old route, how often, and with which auth principal.

---

## Partial Updates

`PATCH` needs an explicit merge rule. The easiest rule for JSON APIs:

- Omitted field: leave unchanged.
- Field set to `null`: clear it, if nullable.
- Field value: replace it.

```python
from pydantic import BaseModel


class TicketPatch(BaseModel):
    title: str | None = None
    assignee_id: str | None = None


@router.patch("/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, patch: TicketPatch):
    changes = patch.model_dump(exclude_unset=True)
    ...
```

Use `exclude_unset=True`; otherwise "field omitted" and "field set to null" collapse into the same thing.

For concurrent edits, use optimistic concurrency:

```text
GET /tickets/t_123
ETag: "version-7"

PATCH /tickets/t_123
If-Match: "version-7"
```

If the version changed, return `412 Precondition Failed`.

---

## OpenAPI Hygiene

OpenAPI is a client contract, not just pretty docs.

Rules:

- Set stable `operation_id` values before clients generate SDKs.
- Hide internal endpoints with `include_in_schema=False`.
- Document error responses, not only success responses.
- Reuse Pydantic models for response bodies.
- Keep examples realistic and scrubbed of secrets.
- Treat OpenAPI diffs as compatibility reviews in CI for public APIs.

```python
@router.get(
    "/tickets/{ticket_id}",
    operation_id="getTicket",
    response_model=TicketRead,
    responses={
        404: {"model": ErrorResponse, "description": "Ticket not found"},
    },
)
async def get_ticket(ticket_id: str):
    ...
```

---

## Checklist

- [ ] Resource names are plural nouns.
- [ ] Unsafe actions are not `GET`.
- [ ] `POST` operations that can be retried use idempotency keys.
- [ ] Collection endpoints paginate.
- [ ] Cursor pagination uses a deterministic sort and opaque cursor.
- [ ] Filters and sorts are allowlisted.
- [ ] Error responses share one envelope.
- [ ] Breaking changes require a new major version or a migration plan.
- [ ] OpenAPI has stable operation IDs and documented errors.
- [ ] Internal endpoints are hidden from the public schema.

---

## References

- [RFC 9110 - HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)
- [RFC 9457 - Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.html)
- [RFC 8288 - Web Linking](https://www.rfc-editor.org/rfc/rfc8288.html)
- [FastAPI OpenAPI docs](https://fastapi.tiangolo.com/how-to/extending-openapi/)

---

## Next

- [07 - Error Responses](07_error_handling.md) - implement the error shape.
- [Safe API Calls / 11 - Idempotency Keys](safe_and_scalable_api_calls/11_idempotency.md) - make retryable `POST`s safe.
