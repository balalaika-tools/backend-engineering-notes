# REST: The Architectural Mental Model

> **Who this is for**: Practitioners who have built JSON endpoints but have not studied what makes an interface RESTful. Start with [API Fundamentals](../01_api_fundamentals.md).

---

## 1️⃣ REST Is a Set of Constraints

**Representational State Transfer (REST)** is an architectural style for networked hypermedia systems. It is not a wire protocol, schema language, or synonym for “HTTP plus JSON.”

REST combines constraints because of the properties they create:

| Constraint | Practical meaning | Property gained | Trade-off |
|------------|-------------------|-----------------|-----------|
| Client-server | UI/consumer concerns are separated from provider storage and logic | Components evolve independently | Boundary and contract must be maintained |
| Stateless | Every request contains the context needed to understand it | Visibility, scalability, independent processing | Repeated context and client-managed application state |
| Cache | Responses declare whether and how they may be reused | Lower latency and load | Invalidation and cache-key correctness |
| Uniform interface | Components use shared resource and message semantics | General tooling and loose coupling | Less operation-specific efficiency |
| Layered system | A client interacts with the next component, not necessarily the origin | Gateways, proxies, CDNs, policy layers | Extra hops and obscured failure sources |
| Code on demand | Server may send executable code | Extensible client behavior | Reduces visibility; optional in REST |

An API can use HTTP correctly without satisfying every REST constraint. Calling such an API “HTTP” or “resource-oriented” is more precise than arguing about labels.

---

## 2️⃣ Resources and Representations

A **resource** is the conceptual target of a reference: a customer, the current exchange rate, an export job, or a collection of open tickets. It is not necessarily one row.

A **representation** is the transferred form of that resource at a point in time.

```text
resource: order ord_123
    │
    ├── JSON representation for API client
    ├── HTML representation for browser
    └── CSV representation for export
```

The server may compute a representation from many tables and services. Consumers should depend on the resource contract, not storage layout.

```http
GET /orders/ord_123 HTTP/1.1
Host: api.example.com
Accept: application/json

HTTP/1.1 200 OK
Content-Type: application/json
ETag: "order-17"

{"id":"ord_123","status":"paid","total_minor":4200,"currency":"EUR"}
```

The URI identifies the resource. `Content-Type` identifies the representation format. `ETag` identifies the selected representation version for caching or preconditions.

---

## 3️⃣ The Uniform Interface

Fielding describes four interface constraints:

1. **Identification of resources** — stable identifiers name conceptual targets.
2. **Manipulation through representations** — requests transfer a desired or current representation rather than reaching into storage.
3. **Self-descriptive messages** — method, URI, headers, media type, and body provide enough semantics for intermediaries and recipients.
4. **Hypermedia as the engine of application state** — responses can provide links and controls for valid next actions.

Most production JSON APIs implement the first three more strongly than the fourth. That makes them useful resource-oriented HTTP APIs, even when they are not a pure hypermedia application.

### Hypermedia example

```json
{
  "id": "ord_123",
  "status": "pending_payment",
  "_links": {
    "self": {"href": "/orders/ord_123"},
    "pay": {"href": "/orders/ord_123/payment", "method": "PUT"},
    "cancel": {"href": "/orders/ord_123/cancellation", "method": "PUT"}
  }
}
```

Hypermedia lets the provider advertise available transitions instead of forcing consumers to construct every URI and state rule from separate documentation. It adds payload and design complexity, so use it when discoverable workflows materially reduce coupling.

---

## 4️⃣ Stateless Does Not Mean “No State”

REST separates two kinds of state:

- **Resource state** belongs on the provider: orders, accounts, jobs, permissions.
- **Application state**—where a consumer is in its workflow—is carried by the consumer and exchanged through requests and representations.

A stateless request can still authenticate with a cookie or bearer token. The constraint is that the server does not need hidden conversation context from request 1 to interpret request 2.

```text
❌ POST /wizard/next
   meaning depends on an opaque server-side step counter

✅ PUT /applications/app_123/employment
   request identifies the resource and supplies the desired representation
```

Server-side sessions are not forbidden technology, but a request whose meaning depends on hidden session conversation state weakens visibility, independent retries, and routing flexibility.

---

## 5️⃣ Why HTTP Is a Natural Fit

HTTP already supplies a uniform vocabulary:

- URIs identify resources.
- Methods describe generic intent.
- Status codes describe result categories.
- Headers carry representation and control metadata.
- Media types describe body formats.
- Validators and cache controls support reuse and concurrency.
- Intermediaries can observe and act without knowing business internals.

If an API returns `200 OK` for every failure, uses `GET` to delete data, or hides cache policy in an SDK, it defeats the shared semantics that make HTTP infrastructure useful.

---

## 6️⃣ Resource-Oriented Example

Model the business concepts before naming routes:

```text
customers
orders
order items
payments
refunds
shipment labels
report runs
```

Then expose lifecycle transitions through resources and methods:

```text
POST   /orders                         create a server-named order
GET    /orders/{order_id}              retrieve its representation
PATCH  /orders/{order_id}              modify allowed fields
PUT    /orders/{order_id}/cancellation create/replace cancellation intent
POST   /orders/{order_id}/refunds      create a refund
POST   /report-runs                    create an asynchronous job
GET    /report-runs/{run_id}           observe job state and result link
```

This is not a ban on verbs. It is a prompt to find durable domain nouns with identity, state, authorization, and lifecycle. `POST /orders/{id}:cancel` can be reasonable when a command truly has no useful resource representation; document its idempotency and state transitions explicitly.

---

## 7️⃣ When REST Is Not the Best Fit

RESTful HTTP is a strong default for broadly consumed resource APIs. Consider another approach when:

- Consumers need arbitrary graph-shaped projections and the server can govern query cost: GraphQL.
- Owned services benefit from generated RPC types and streaming: gRPC.
- Both parties frequently initiate messages over one persistent channel: WebSocket.
- A provider must notify an independently hosted receiver: webhook.
- An existing contract requires SOAP/WSDL or particular WS-* behavior: SOAP.

Do not add a second API technology to repair a bad domain model. First confirm the requirement is interaction-level, not merely inconsistent endpoints or slow database access.

---

## References

- [Fielding dissertation, Chapter 5: REST](https://ics.uci.edu/~fielding/pubs/dissertation/rest_arch_style.htm)
- [HTTP Semantics — RFC 9110](https://www.rfc-editor.org/rfc/rfc9110)

---

**Next**: [HTTP Semantics](02_http_semantics.md)

