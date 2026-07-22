# HTTP Semantics for RESTful APIs

> **Who this is for**: API designers who need clients, retries, caches, proxies, and monitoring to interpret operations consistently. Assumes [REST Mental Model](01_rest_mental_model.md).

---

## 1. Semantics Are Independent of HTTP Version

HTTP/1.1, HTTP/2, and HTTP/3 use different framing and transports, but share the semantics defined by RFC 9110.

```text
semantic message
method + target + fields + optional content + response status
          │
          ├── encoded as HTTP/1.1 text-oriented messages
          ├── encoded as HTTP/2 frames over TCP
          └── encoded as HTTP/3 frames over QUIC
```

Application code should not assume that one request maps to one connection or that header names retain a particular casing. Proxies and clients parse and reconstruct messages.

---

## 2. Safety, Idempotency, and Cacheability

These properties are different:

- **Safe**: the requested semantics are read-only. Incidental logging or billing metrics do not make a `GET` unsafe.
- **Idempotent**: repeating the same request has the same intended server-side effect as sending it once.
- **Cacheable**: a response may be stored and reused under HTTP caching rules.

| Method | Intended use | Safe | Idempotent | Typical response cacheability |
|--------|--------------|------|------------|-------------------------------|
| `GET` | Transfer a selected representation | Yes | Yes | Yes unless controls prevent it |
| `HEAD` | Same metadata as `GET`, without response content | Yes | Yes | Yes |
| `POST` | Let the target process enclosed content | No | No by default | Defined, but many caches do not implement it |
| `PUT` | Create or replace the state at the target URI | No | Yes | Normally not cacheable |
| `PATCH` | Apply a patch document | No | Depends on patch semantics | Normally not cacheable |
| `DELETE` | Remove the target resource association | No | Yes | Normally not cacheable |
| `OPTIONS` | Discover communication options | Yes | Yes | Normally not cacheable |

Idempotency concerns intended state, not identical responses. Two `DELETE` requests may return `204` then `404`; the resource is absent after both.

### `GET` content

RFC 9110 does not assign generally applicable semantics to request content on `GET`. Some servers privately define it, but clients, caches, gateways, and frameworks may ignore or reject it. Use query parameters for ordinary retrieval or `POST /searches` / `POST /reports/query` when a complex or sensitive request document is necessary.

Never place a destructive action behind `GET`. Crawlers, link previews, caches, and prefetchers are allowed to invoke safe methods.

---

## 3. Choosing the Method

| Intent | Recommended shape | Notes |
|--------|-------------------|-------|
| Read a resource | `GET /orders/{id}` | Return cache validators where useful |
| Create with server-selected ID | `POST /orders` | Return `201` and `Location` |
| Create/replace at client-known URI | `PUT /profiles/{id}` | Full replacement semantics must be clear |
| Partial modification | `PATCH /orders/{id}` | Declare patch media type and concurrency behavior |
| Delete | `DELETE /orders/{id}` | Decide repeated-delete response policy |
| Start long work | `POST /report-runs` | Return `202` or `201` with status resource |
| Complex query | `POST /order-searches` | Document read-only semantics and caching limitations |

`POST` does not mean “create only.” It asks the target resource to process the content according to target-specific semantics. That includes commands, queries, calculations, and subordinate resource creation.

---

## 4. Status Codes as a Shared Vocabulary

Use a small consistent set, then add codes only when consumers can act differently.

### Success and redirection

| Code | Use |
|------|-----|
| `200 OK` | Successful result with content |
| `201 Created` | One or more resources created; use `Location` for the primary resource |
| `202 Accepted` | Accepted for processing, not completed; link a status resource |
| `204 No Content` | Successful result with no response content |
| `304 Not Modified` | Conditional retrieval may reuse its cached representation |

A `202` is not success of the business operation. It is acceptance. The status resource needs terminal success/failure state, progress if meaningful, expiry, and result or problem details.

### Client-related failures

| Code | Use |
|------|-----|
| `400 Bad Request` | Malformed syntax or a generic request problem |
| `401 Unauthorized` | Missing/invalid authentication; include the applicable challenge when required |
| `403 Forbidden` | Identity is known but action is not permitted |
| `404 Not Found` | Resource absent or intentionally hidden from this principal |
| `405 Method Not Allowed` | Resource exists but method is unsupported; include `Allow` |
| `409 Conflict` | Request conflicts with current resource state |
| `412 Precondition Failed` | `If-Match` or another HTTP precondition failed |
| `415 Unsupported Media Type` | Request representation format unsupported |
| `422 Unprocessable Content` | Syntax understood but instructions/content invalid |
| `428 Precondition Required` | Server requires a conditional request to prevent lost updates |
| `429 Too Many Requests` | Rate control rejected the request; communicate retry policy |

Do not reveal whether a resource exists when that leaks sensitive tenant or account information. A scoped lookup that returns `404` can be safer than distinguishing “exists but forbidden.”

### Provider and intermediary failures

| Code | Use |
|------|-----|
| `500 Internal Server Error` | Unexpected provider failure |
| `502 Bad Gateway` | Gateway received an invalid/failing upstream result |
| `503 Service Unavailable` | Temporary overload or maintenance |
| `504 Gateway Timeout` | Gateway did not receive an upstream response in time |

Clients should not retry solely because a code is `5xx`. Operation safety, remaining deadline, attempt budget, and server guidance all matter.

---

## 5. Headers That Carry Contract Semantics

| Header | Direction | Purpose |
|--------|-----------|---------|
| `Content-Type` | Both | Media type of enclosed content |
| `Accept` | Request | Response media types acceptable to the client |
| `Authorization` | Request | Credentials for the target resource |
| `Location` | Response | URI associated with creation or redirection |
| `ETag` | Response | Validator for the selected representation |
| `If-None-Match` | Request | Conditional read or create-if-absent pattern |
| `If-Match` | Request | Conditional write against a known version |
| `Cache-Control` | Both | Storage and reuse directives |
| `Vary` | Response | Request fields that participate in cache selection |
| `Retry-After` | Response | When a client may try again for applicable status codes |
| `Traceparent` | Request/response ecosystem | Distributed trace context; not a business identifier |

Business fields usually belong in the representation, not invented `X-*` headers. Use headers for message metadata understood independently of one resource schema.

---

## 6. Content Negotiation

The sender describes content with `Content-Type`; the recipient expresses preferences with `Accept`.

```http
POST /orders HTTP/1.1
Content-Type: application/json
Accept: application/json
```

Return `415` when the request format is unsupported. Return `406 Not Acceptable` when the server cannot produce an acceptable representation, if the API chooses to enforce negotiation strictly.

If a response changes by `Accept-Encoding`, `Accept-Language`, `Origin`, or another request field and shared caching is possible, configure `Vary` correctly. Missing `Vary` can serve one user's or origin's variant to another.

---

## 7. Intermediaries Are Participants

```text
client → CDN → API gateway → service mesh proxy → application
```

Each intermediary may enforce size limits, cache, retry, normalize headers, terminate TLS, buffer streaming, or impose its own timeout. Document and test the effective end-to-end behavior.

Common failures:

- Gateway times out at 30 seconds while application promises 60.
- CDN caches personalized content because `Cache-Control` or `Vary` is wrong.
- Proxy automatically retries an operation the application considered non-idempotent.
- Framework trusts spoofed forwarding headers from an untrusted direct client.
- Load balancer rejects large headers before application validation runs.

> **Key insight**: HTTP's uniform semantics create leverage only when every hop preserves them.

---

## References

- [HTTP Semantics — RFC 9110](https://www.rfc-editor.org/rfc/rfc9110)
- [HTTP Caching — RFC 9111](https://www.rfc-editor.org/rfc/rfc9111)
- [Additional HTTP Status Codes — RFC 6585](https://www.rfc-editor.org/rfc/rfc6585)

---

**Next**: [Resource and URI Design](03_resource_and_uri_design.md)

