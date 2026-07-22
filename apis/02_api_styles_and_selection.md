# API Styles and Selection

> **Who this is for**: Engineers choosing an integration style after learning the contract model in [API Fundamentals](01_api_fundamentals.md).

---

## 1️⃣ Compare the Right Things

REST, SOAP, GraphQL, gRPC, WebSocket, and webhooks solve overlapping but different problems.

| Choice | What it is | Normal interaction | Strongest fit |
|--------|------------|--------------------|---------------|
| REST | Architectural style commonly realized with HTTP | Request-response | Resource-oriented public and internal APIs |
| SOAP | XML messaging protocol with a large standards ecosystem | Request-response or messaging | Existing enterprise contracts and WS-* requirements |
| GraphQL | Typed query language and execution model | Request-response; subscriptions possible | Client-selected data across a connected domain graph |
| gRPC | RPC framework commonly using Protobuf and HTTP/2 | Unary and streaming RPCs | Typed service-to-service calls in controlled environments |
| WebSocket | Persistent, full-duplex message protocol | Bidirectional messages | Frequent low-latency updates in both directions |
| Webhook | Provider-to-consumer HTTP callback pattern | Asynchronous event notification | Cross-organization event delivery without persistent connections |

The choices can coexist. A product might expose REST publicly, use gRPC internally, notify partners through webhooks, and power collaboration through WebSockets.

---

## 2️⃣ Decision Matrix

| Requirement | Default choice | Why | Check before committing |
|-------------|----------------|-----|-------------------------|
| Broad browser, mobile, CLI, and partner compatibility | REST over HTTPS | Universal tooling, HTTP semantics, cache support | Does the domain fit resources and standard methods? |
| Each screen needs a different projection of a connected data graph | GraphQL | Consumer selects fields and nested relations | Can the team control query cost and resolver performance? |
| Low-latency calls between owned services in several languages | gRPC | Generated types, compact messages, streaming, deadlines | Do proxies, debuggers, and clients support the stack? |
| Frequent two-way interactive messages | WebSocket | One persistent duplex channel | Can you own reconnection, message contracts, backpressure, and fleet state? |
| Notify another system after an event | Webhook | Receiver needs only an HTTPS endpoint | Can both sides handle duplicates, delay, signatures, retries, and replay? |
| Formal XML contracts, intermediaries, or required WS-Security features | SOAP | Mature standards and enterprise tooling | Is this a real constraint or only organizational habit? |
| Infrequent one-way browser updates | SSE or polling | Simpler than a duplex socket | Does the client ever need to send on the same channel? |

> **Rule**: Choose the simplest mechanism that satisfies the interaction. Persistent connections and flexible query engines have real operational cost.

---

## 3️⃣ Strengths and Trade-offs

### RESTful HTTP

✅ Human-readable, broadly supported, gateway-friendly, and able to use standardized caching and conditional requests.

⚠️ A poorly modeled REST API becomes inconsistent RPC disguised as URLs. Multiple round trips or fixed representations can be awkward for complex UI aggregation.

### SOAP

✅ Precise XML contracts, formal faults, extensible headers, and established standards for some enterprise requirements.

⚠️ Verbose messages, complex tooling, and WS-* profiles add weight. Starting a greenfield SOAP API without a concrete interoperability requirement is rarely the simplest choice.

### GraphQL

✅ One typed graph lets consumers request the exact connected shape they need. Schema introspection and client tooling are strong.

⚠️ Field-level authorization, query cost, caching, N+1 access, and compatibility move into the GraphQL execution layer. It does not make expensive joins free.

### gRPC

✅ Code generation, compact binary payloads, four RPC streaming modes, deadlines, metadata, and standardized status codes work well for service calls.

⚠️ Binary debugging and browser support require different tooling. Generated clients and strict schema discipline are part of the workflow.

### WebSocket

✅ Low-overhead, full-duplex delivery after connection establishment.

⚠️ The protocol supplies a channel, not event schemas, acknowledgements, resumption, authorization rules, or backpressure policy. Long-lived connections also change deployment and capacity planning.

### Webhooks

✅ Cross-system push without polling or a permanent connection.

⚠️ The receiver may be unavailable, slow, compromised, or duplicated. Producers need durable delivery state; consumers need verification and idempotency.

---

## 4️⃣ A Practical Selection Flow

```text
Does another system only need notification after an event?
├── yes → webhook (plus a read API for reconciliation)
└── no
    │
    ├── Need frequent messages initiated by both sides?
    │   └── yes → WebSocket
    │
    ├── Owned service-to-service clients, generated types, streaming?
    │   └── yes → gRPC
    │
    ├── Consumer must select a graph-shaped response dynamically?
    │   └── yes → GraphQL
    │
    ├── Required WSDL / WS-* enterprise interoperability?
    │   └── yes → SOAP
    │
    └── default → RESTful HTTP
```

This is a starting point, not a scoring algorithm. Network policy, team expertise, ecosystem constraints, existing consumers, and operational tooling can outweigh technical preference.

---

## 5️⃣ Worked Architecture: Order Platform

```text
browser/mobile ──REST/GraphQL──> API edge
                                  │
                                  ├──gRPC──> inventory service
                                  ├──gRPC──> pricing service
                                  └──REST──> payment provider

API edge ──WebSocket──> browser order-status screen
order service ──webhook──> merchant fulfillment system
legacy ERP <──SOAP──> integration adapter
```

Why each boundary differs:

- Public clients favor accessible HTTP tooling and explicit compatibility.
- Owned internal services benefit from generated gRPC clients and deadlines.
- The status screen needs timely server pushes and also sends client acknowledgements.
- Merchants cannot hold inbound connections to the platform, so the platform calls their webhook endpoints.
- The existing ERP contract already requires WSDL and SOAP; an adapter contains that complexity.

Trying to standardize every arrow on one technology would make at least one boundary worse.

---

## 6️⃣ Common Selection Mistakes

| Mistake | Why it fails | Better question |
|---------|--------------|-----------------|
| “GraphQL removes over-fetching, so use it everywhere” | Runtime cost and authorization become query-dependent | Do consumers truly need arbitrary projections? |
| “WebSockets are faster HTTP” | They introduce connection state and recovery work | Is frequent bidirectional push required? |
| “gRPC is always faster” | End-to-end latency may be database or dependency bound | Is serialization/transport material, and can every hop support it? |
| “Webhooks guarantee real-time delivery” | Delivery is delayed by outages, retries, and queues | What staleness and reconciliation guarantees are required? |
| “JSON endpoints are REST” | JSON says nothing about REST constraints or HTTP semantics | Does the interface model resources and use a uniform, self-descriptive interface? |
| “Internal APIs can break freely” | Independent deployments and hidden consumers create coupling | How will old and new versions overlap during rollout? |

---

## References

- [REST architectural style](https://ics.uci.edu/~fielding/pubs/dissertation/rest_arch_style.htm)
- [GraphQL specification](https://spec.graphql.org/)
- [gRPC core concepts](https://grpc.io/docs/what-is-grpc/core-concepts/)
- [The WebSocket Protocol — RFC 6455](https://www.rfc-editor.org/rfc/rfc6455)
- [SOAP 1.2 Messaging Framework](https://www.w3.org/TR/soap12-part1/)

---

**Next**: [API Contracts and Lifecycle](03_api_contracts_and_lifecycle.md)

