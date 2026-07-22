# GraphQL: A Focused Overview

> **Who this is for**: Engineers who understand request-response APIs and need to recognize when GraphQL's client-selected graph is useful.

---

## 1️⃣ The Mental Model

**GraphQL** is a query language and execution model built around a typed schema. A consumer selects fields from that schema, and the response mirrors the requested shape.

```graphql
query OrderScreen($orderId: ID!) {
  order(id: $orderId) {
    id
    status
    customer { displayName }
    items { quantity product { name } }
  }
}
```

```json
{
  "data": {
    "order": {
      "id": "ord_123",
      "status": "PAID",
      "customer": {"displayName": "Ada"},
      "items": [{"quantity": 2, "product": {"name": "Keyboard"}}]
    }
  }
}
```

The server exposes a graph of fields, not direct database access. **Resolvers** obtain each field from databases, services, caches, or computed logic while enforcing authorization.

---

## 2️⃣ Schema and Operations

```graphql
type Query {
  order(id: ID!): Order
}

type Mutation {
  cancelOrder(input: CancelOrderInput!): CancelOrderPayload!
}

type Order {
  id: ID!
  status: OrderStatus!
  items(first: Int = 20, after: String): OrderItemConnection!
}

input CancelOrderInput {
  orderId: ID!
  idempotencyKey: String!
}

type CancelOrderPayload {
  order: Order
  problems: [UserProblem!]!
}
```

| Operation | Purpose | Important detail |
|-----------|---------|------------------|
| Query | Read data | Should not cause business side effects |
| Mutation | Change state | Top-level mutation fields execute serially; downstream effects still need idempotency |
| Subscription | Receive result events over time | Requires a transport and recovery design beyond the core field schema |

Non-null `!` is a runtime promise. If a non-null field resolves to null, the error propagates to the nearest nullable parent and can remove a larger subtree from `data`. Use non-null intentionally, especially around unreliable dependencies.

---

## 3️⃣ Execution and the N+1 Problem

A nested query can become one parent query plus one database call per item:

```text
load 100 orders                    1 query
resolve customer for each order  100 queries
                                   ─────────
                                   101 total
```

Batch loaders collect keys during one execution turn, fetch them together, and cache within that request:

```text
customer resolver keys: [c1, c8, c1, c4]
batch query: WHERE id IN (c1, c8, c4)
map results back to resolver positions
```

The cache must normally be **request-scoped**. A global loader cache can return another tenant's data or stale authorization decisions.

GraphQL solves response-shape flexibility. It does not solve storage access, distributed joins, or latency automatically.

---

## 4️⃣ Security and Cost Controls

Authentication at the HTTP edge is only the first gate. Enforce authorization at the business object or field boundary, including nested resolvers.

Production controls commonly include:

- Maximum parsed document size
- Maximum depth and alias count
- Static or estimated query-cost budget
- Pagination caps on every list field
- Resolver and overall operation deadlines
- Per-principal rate and cost quotas
- Batching to prevent N+1 access
- Persisted or allowlisted operations for controlled clients
- Introspection policy appropriate to the exposure model
- Redacted errors that keep stable machine-readable extensions

Disabling introspection is not an authorization control. Attackers can infer fields through other means; every resolver must still reject unauthorized access.

---

## 5️⃣ Evolution

GraphQL usually evolves one schema rather than placing a version in the URL.

Generally safe:

- Add a nullable field.
- Add an optional argument with a default.
- Add a type that existing operations do not reference.

Breaking or behaviorally risky:

- Remove or rename a field or enum value.
- Change a field's type or nullability.
- Make an optional argument required.
- Change resolver meaning or authorization.
- Add an enum value when clients use exhaustive switches.

Deprecate fields with a reason, measure real operation usage, provide a replacement, then remove under a published policy. Schema reachability alone does not prove a field is used.

---

## 6️⃣ When to Use It

Choose GraphQL when multiple trusted or semi-trusted client experiences need different connected projections and the team can operate a query execution platform.

Prefer REST or gRPC when:

- Operations have fixed, cacheable representations.
- Simple HTTP/CDN semantics are a central requirement.
- The consumers do not need arbitrary field selection.
- Strict per-operation cost and allowlisting are more valuable than flexibility.
- Service-to-service generated RPC contracts match the domain better.

GraphQL and REST can coexist: GraphQL may serve as a client-facing aggregation layer while domain services expose resource or RPC interfaces internally.

---

## References

- [GraphQL Specification, September 2025](https://spec.graphql.org/September2025/)
- [GraphQL project](https://graphql.org/)

---

**Next**: [gRPC Overview](06_grpc_overview.md)

