# API Communication Guides

> Protocol-neutral notes for choosing, designing, securing, evolving, and operating service interfaces.

[![HTTP](https://img.shields.io/badge/HTTP-RFC_9110-005C9C.svg)](https://www.rfc-editor.org/rfc/rfc9110)
[![OpenAPI](https://img.shields.io/badge/OpenAPI-3.x-6BA539.svg?logo=openapiinitiative&logoColor=white)](https://spec.openapis.org/oas/)
[![GraphQL](https://img.shields.io/badge/GraphQL-Specification-E10098.svg?logo=graphql&logoColor=white)](https://spec.graphql.org/)
[![gRPC](https://img.shields.io/badge/gRPC-Protocol-244C5A.svg)](https://grpc.io/)

---

## Contents

### Shared Foundations

| File | Topic | Description |
|------|-------|-------------|
| [01_api_fundamentals.md](01_api_fundamentals.md) | API fundamentals | Contracts, endpoints, messages, interaction models, and distributed failure |
| [02_api_styles_and_selection.md](02_api_styles_and_selection.md) | Style selection | REST, SOAP, GraphQL, gRPC, WebSocket, and webhook decision guide |
| [03_api_contracts_and_lifecycle.md](03_api_contracts_and_lifecycle.md) | Contracts and lifecycle | Schema design, compatibility, governance, documentation, and deprecation |

### Focused Overviews

| File | Topic | Description |
|------|-------|-------------|
| [04_soap_overview.md](04_soap_overview.md) | SOAP | Envelopes, WSDL, XML Schema, faults, WS-* capabilities, and legacy integration |
| [05_graphql_overview.md](05_graphql_overview.md) | GraphQL | Schemas, operations, resolvers, N+1 queries, security, and selection criteria |
| [06_grpc_overview.md](06_grpc_overview.md) | gRPC | Protobuf contracts, RPC types, deadlines, compatibility, and browser constraints |

### Deep Dives

| Section | Description |
|---------|-------------|
| [RESTful APIs](restful/README.md) | REST constraints, HTTP semantics, resource design, errors, collections, reliability, caching, security, evolution, and operations |
| [WebSockets](websockets/README.md) | Handshakes, frames, application protocols, reconnection, flow control, security, scaling, testing, and operations |
| [Webhooks](webhooks/README.md) | Event contracts, producer and consumer architecture, signatures, SSRF, retries, idempotency, replay, and operations |

---

## Reading Order

1. **Build the vocabulary** — read [API Fundamentals](01_api_fundamentals.md).
2. **Choose an interaction model** — use [API Styles and Selection](02_api_styles_and_selection.md).
3. **Design for change** — read [Contracts and Lifecycle](03_api_contracts_and_lifecycle.md).
4. **Follow one branch** — continue into RESTful APIs, WebSockets, webhooks, or a focused overview.

---

## Prerequisites

- Familiarity with client-server applications and basic networking
- [HTTPX](../fundamentals/httpx/README.md) for Python HTTP client mechanics
- [FastAPI](../fundamentals/fastapi/README.md) for framework-specific implementations

