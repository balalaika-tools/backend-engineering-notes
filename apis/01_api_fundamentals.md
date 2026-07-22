# API Fundamentals: Contracts Across a Boundary

> **Who this is for**: Backend practitioners who have used HTTP endpoints but want a protocol-neutral mental model of APIs.

---

## 1️⃣ What Problem an API Solves

An **application programming interface (API)** lets one component use another component without depending on its internal implementation. The useful abstraction is not “a URL that returns JSON.” It is an owned contract across a boundary.

```text
consumer                    provider
┌─────────────────┐         ┌──────────────────────────┐
│ knows contract  │ message │ owns implementation      │
│ not internals   │────────>│ validates and authorizes │
│ handles outcome │<────────│ returns outcome          │
└─────────────────┘         └──────────────────────────┘
```

The contract answers:

- Which operations or events exist?
- What data can cross the boundary?
- Who may use it?
- What does success mean?
- How are failures represented?
- What timing, ordering, and delivery behavior can callers rely on?
- How may the contract evolve?

A Python function has an API. So does a database driver, an operating system, an HTTP service, or a message broker. This guide focuses on **network APIs**, where partial failure and independent deployment make the contract harder to enforce.

> **Key insight**: An API hides implementation details, not operational reality. Latency, authorization, quotas, retries, compatibility, and failure semantics are part of the interface even when the schema omits them.

---

## 2️⃣ Core Vocabulary

| Term | Meaning | Example |
|------|---------|---------|
| **Provider** | Component that implements the interface | Billing service |
| **Consumer** | Component that calls or subscribes to it | Checkout service |
| **Contract** | Machine- and human-readable rules of interaction | OpenAPI document plus behavioral rules |
| **Operation** | A capability the consumer invokes | Create an invoice |
| **Endpoint** | A network-reachable address for an operation or channel | `POST /invoices`, `wss://api.example.com/events` |
| **Resource** | Conceptual target identified by an API | Invoice `inv_123` |
| **Representation** | Transferred form of a resource or result | JSON invoice document |
| **Message** | Discrete application unit sent across the boundary | Request, response, WebSocket message, webhook event |
| **Event** | Statement that something happened | `invoice.paid` |
| **Schema** | Structural rules for data | JSON Schema, GraphQL SDL, XML Schema, Protobuf |
| **Protocol** | Rules for message exchange | HTTP, SOAP, WebSocket |

Do not use these as synonyms. A resource is not its database row, a representation is not the resource itself, and an endpoint is not the whole API.

---

## 3️⃣ The Independent Design Dimensions

Technologies are easier to compare when separated into layers:

```text
business contract   operations, events, authorization, compatibility
        ↓
interaction model   request/response, stream, full duplex, callback
        ↓
API style/tool       REST, GraphQL, gRPC, SOAP, webhook pattern
        ↓
representation      JSON, XML, Protocol Buffers, binary frames
        ↓
transport           TCP/TLS or QUIC beneath HTTP and WebSocket
        ↓
network security    TLS, identity, firewall and routing policy
```

These choices are not one-to-one:

- REST commonly uses HTTP and JSON, but REST is an architectural style, not a JSON protocol.
- SOAP messages are XML and may be bound to HTTP, but SOAP is not defined as “XML over HTTP only.”
- GraphQL commonly uses HTTP for queries and mutations and may use a persistent transport for subscriptions.
- gRPC normally combines generated stubs, Protocol Buffers, and HTTP/2.
- A webhook is usually an outbound HTTP request carrying an event; it is a delivery pattern, not a new transport.
- WebSocket supplies a duplex channel but does not define the application messages sent through it.

> **Rule**: First choose the interaction properties the product needs. Choose a technology only after identifying the contract, clients, network path, and failure model.

---

## 4️⃣ Interaction Models

| Model | Flow | Best fit | Main cost |
|-------|------|----------|-----------|
| Request-response | Consumer asks; provider answers once | CRUD, commands, queries | Consumer initiates every exchange |
| Server streaming | One request; a sequence of responses | Feeds, large result streams | Long-lived resource and recovery logic |
| Client streaming | Sequence of inputs; one result | Telemetry upload, aggregation | Flow control and partial input failure |
| Bidirectional streaming | Both sides send independently | Collaboration, games, interactive control | Stateful connections and an application protocol |
| Callback/webhook | Provider calls a consumer URL after an event | Cross-system notifications | Delivery is asynchronous and usually at-least-once |
| Polling | Consumer periodically asks for change | Simple or low-frequency updates | Delay and waste increase with frequency |

“Synchronous” and “asynchronous” are overloaded:

- A request-response API can be implemented with Python `async` I/O but still make the caller wait for a result.
- `202 Accepted` can acknowledge a request synchronously while the actual work completes asynchronously.
- A webhook is asynchronous from the original business operation even though each delivery is an ordinary synchronous HTTP exchange.

---

## 5️⃣ A Request Is a Distributed Operation

A service call passes through more than application code:

```text
client
  → DNS → connection/TLS → proxy or gateway → load balancer
  → server runtime → authentication → authorization → validation
  → business logic → database/dependency
  → serialization → reverse network path
```

Each arrow can fail independently. The caller may observe a timeout even though the server committed the change. That creates an **ambiguous outcome**:

```text
client              server             database
  │ POST order         │                    │
  │───────────────────>│── COMMIT ─────────>│
  │                    │<───────────────────│
  │      response lost X                    │
  │ timeout             │                    │
```

Blindly retrying can now create two orders. Safe designs combine method semantics, idempotency keys, deduplication, conditional updates, and reconciliation. See [REST: Concurrency, Idempotency, and Retries](restful/06_concurrency_idempotency_and_retries.md).

> **Principle**: A timeout says the caller stopped waiting. It does not prove the server did nothing.

---

## 6️⃣ The Four Parts of a Real Contract

| Contract layer | Questions it must answer |
|----------------|--------------------------|
| Structural | Which fields, types, operations, and event names are valid? |
| Semantic | What does each field mean? Which invariants and state transitions apply? |
| Operational | Deadlines, rate limits, size limits, ordering, retries, pagination, availability |
| Security | Identity, authorization, tenant boundaries, sensitive fields, audit requirements |

A schema can prove that `amount` is an integer. It cannot, by itself, explain whether it is cents, whether negative values are legal, which account it debits, or whether repeating the request is safe.

### Minimal operation contract

```yaml
operation: create_payment
input:
  amount_minor: integer       # ISO currency minor units, not a float
  currency: ISO-4217 string
  customer_id: string
success:
  payment_id: string
  status: pending | succeeded | failed
behavior:
  authentication: OAuth access token
  authorization: customer belongs to caller's tenant
  idempotency: required; same key and same payload return same operation
  timeout: client should stop waiting after 10 seconds
  rate_limit: communicated by response headers
  compatibility: additive response fields may appear
```

The comments are not optional documentation. They carry meaning the type system cannot express.

---

## 7️⃣ API Exposure Changes the Design

| Exposure | Typical consumers | Design implication |
|----------|-------------------|--------------------|
| Internal | Teams under one organization | Coordinated migration is possible, but compatibility still matters |
| Partner | Selected organizations | Strong onboarding, tenant controls, support, and explicit change policy |
| Public | Unknown independent clients | Long support windows, abuse resistance, stable docs, quotas, and SDK concerns |

“Internal” does not mean “safe to break.” Internal services often deploy independently, have hidden consumers, and sit on critical paths. Inventory consumers and measure usage before removal.

---

## 8️⃣ Definition of Done for an API

Before production, answer all of these:

- The intended consumer and the problem are explicit.
- Operations and data have a documented contract.
- Authentication and object-level authorization are defined.
- Timeouts, limits, retryability, and idempotency are documented.
- Errors are machine-readable and do not leak internals.
- Backward-compatibility rules are tested automatically.
- Logs, traces, metrics, and request or event identifiers support diagnosis.
- Owners, support path, SLOs, versioning, and deprecation policy exist.
- Examples cover failure as well as success.

An endpoint that returns the right JSON once is a prototype. An API is a maintained product boundary.

---

## References

- [HTTP Semantics — RFC 9110](https://www.rfc-editor.org/rfc/rfc9110)
- [REST architectural style — Fielding dissertation, Chapter 5](https://ics.uci.edu/~fielding/pubs/dissertation/rest_arch_style.htm)
- [OpenAPI Specification](https://spec.openapis.org/oas/)

---

**Next**: [API Styles and Selection](02_api_styles_and_selection.md)
