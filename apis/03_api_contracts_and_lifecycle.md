# API Contracts and Lifecycle

> **Who this is for**: API owners who need independently deployed consumers to survive change. Assumes [API Fundamentals](01_api_fundamentals.md).

---

## 1. Treat the Contract as a Product

The interface is the part consumers build against. A production contract includes more than a schema:

```text
contract
├── structure     operations, fields, types, formats
├── behavior      invariants, transitions, side effects
├── failure       error taxonomy, retryability, partial success
├── operations    deadlines, limits, ordering, availability
├── security      identity, authorization, sensitive data
└── evolution     compatibility, versions, deprecation policy
```

If a consumer must learn a rule from an incident or provider source code, the contract is incomplete.

---

## 2. Contract Technologies

| API family | Typical contract artifact | What it describes well |
|------------|---------------------------|------------------------|
| RESTful HTTP | OpenAPI plus JSON Schema | Paths, methods, parameters, payloads, responses, security schemes |
| SOAP | WSDL plus XML Schema | Operations, messages, XML types, bindings, service locations |
| GraphQL | GraphQL schema definition language | Types, fields, arguments, operations, nullability, directives |
| gRPC | Protocol Buffer `.proto` files | Services, RPC methods, messages, field numbers, streaming shape |
| WebSocket | AsyncAPI or a documented message schema | Channels, message envelopes, directions, correlation |
| Webhook | Event catalog plus JSON Schema/OpenAPI/AsyncAPI | Event types, envelope, payload versions, delivery headers |

Machine-readable artifacts enable generation and checks, but descriptions still need units, invariants, authorization, examples, and operational behavior.

### Schema-first vs code-first

| Approach | Benefit | Risk |
|----------|---------|------|
| Schema-first | Consumers review the boundary before implementation; generation is predictable | Schema can drift if implementation tests are weak |
| Code-first | Low friction and close to implementation types | Internal models and framework defaults can leak into a public contract |

Both can work. The non-negotiable control is **contract conformance in CI**: validate examples, diff changes, run consumer or provider contract tests, and publish only from the tested revision.

---

## 3. Lifecycle

```text
discover → design → review → implement → verify → publish → operate → deprecate → retire
              ↑                                      │
              └──────────── usage and feedback ──────┘
```

### Discover

Identify consumers, business outcomes, data classification, latency needs, ownership, and alternatives. Do not start with endpoint names.

### Design and review

Review the resource or operation model, error behavior, authorization, abuse cases, compatibility, and operational limits. Include consumer representatives.

### Implement and verify

Validate both directions: requests against input schemas and responses/events against published output schemas. Add integration, negative, compatibility, and load tests.

### Publish and operate

Publish the artifact, examples, changelog, ownership, support path, SLOs, limits, and credentials workflow. Observe operations by stable operation and error identifiers—not raw paths containing IDs.

### Deprecate and retire

Announce an alternative and timeline, measure remaining use, contact consumers, stop new adoption, and remove only after the stated policy allows it. A warning without usage telemetry is guesswork.

---

## 4. Compatibility Rules

Compatibility is about old and new producers and consumers overlapping during deployment.

| Change | Usually safe? | Hidden risk |
|--------|---------------|-------------|
| Add optional response field | Often | Strict decoders may reject unknown fields |
| Add optional request field | Often | Old providers ignore it; semantics must remain optional |
| Add enum value | Risky | Exhaustive switches can fail |
| Make optional field required | No | Existing requests or stored messages omit it |
| Rename or remove field | No | Consumers still read or send it |
| Change field meaning/unit | No | Wire shape stays valid while behavior corrupts |
| Tighten validation | Risky | Previously accepted traffic begins failing |
| Add a new error or status | Risky | Retry and UX behavior may change |
| Reorder JSON fields | Yes | A signature scheme over reserialized JSON may disagree |

Design consumers with the **robustness rule** appropriate to the format: ignore unknown additive fields, handle unknown enum values defensively, and do not infer meaning from field order. Providers must not silently change semantics.

> **Rule**: “The schema still validates” is weaker than “existing consumers still behave correctly.”

### Expand, migrate, contract

For a rename from `customer_name` to `display_name`:

1. Add `display_name`; continue accepting and returning the old field where required.
2. Migrate producers and consumers while measuring both fields.
3. Stop new use and announce retirement.
4. Remove `customer_name` only after the compatibility window.

This pattern applies to HTTP payloads, GraphQL fields, event schemas, and storage migrations.

---

## 5. Versioning Strategy

Prefer compatible evolution within one version. Introduce a new major version when semantics cannot coexist safely.

| Strategy | Advantages | Costs |
|----------|------------|-------|
| Path, `/v2/orders` | Visible and easy to route | Resource identity and links can fragment |
| Header/media type | Keeps stable paths | Harder to explore, cache, and support |
| Date or revision | Precise behavior snapshot | Many supported combinations can accumulate |
| Schema evolution without global version | Smooth additive change | Requires excellent field-level discipline |

Version the contract, not the server deployment. `/v2` must describe a coherent compatibility boundary; it is not a release number.

For events, include an explicit event `type` and schema version or make event type names versioned. Do not let the consumer infer version from the current provider deployment.

---

## 6. Documentation That Consumers Need

Every operation or event should document:

- Purpose and when not to use it
- Authentication and required permissions
- Request or message schema with realistic examples
- Success, error, and partial-success behavior
- Idempotency and retry policy
- Timeouts, rate/size limits, pagination, ordering, and retention
- Compatibility and deprecation rules
- Test environment, sample credentials, and replay fixtures
- Support owner and request/event identifiers used for diagnosis

Generate references from the contract, but write task-oriented guides by hand. Generated field tables cannot explain a safe payment retry or webhook recovery workflow.

---

## 7. Governance and Automation

An effective pipeline makes unsafe change visible:

```text
contract change
  → syntax/schema lint
  → breaking-change diff
  → security and ownership review
  → generated clients/docs
  → provider conformance tests
  → consumer contract tests
  → deploy with old/new overlap
  → observe version and operation usage
```

Useful controls:

- Central registry/catalog with owners and lifecycle state
- Naming, pagination, error, authentication, and event-envelope standards
- Schema diff gates with an explicit exception process
- Examples validated as part of the build
- Synthetic checks against deployed endpoints
- Dependency and consumer inventory
- Dashboards for traffic, errors, latency, saturation, retries, and deprecated use

Governance should make the safe path easy. A style guide that is not enforced by templates, linters, and tests becomes archaeology.

---

## 8. Contract Review Checklist

- [ ] The consumer and business capability are named.
- [ ] Schema and semantics agree; units and invariants are explicit.
- [ ] Authorization is defined per operation, object, and sensitive property.
- [ ] Retryable and terminal failures are distinguishable.
- [ ] Idempotency, ordering, and concurrency behavior are documented.
- [ ] Size, rate, cost, deadline, and retention limits are stated.
- [ ] Additive and breaking-change policies are testable.
- [ ] Examples include malformed, forbidden, conflict, timeout, and duplicate cases.
- [ ] Logs and traces correlate a call without recording secrets.
- [ ] Ownership, SLO, support, deprecation, and retirement are defined.

---

## References

- [OpenAPI Specification](https://spec.openapis.org/oas/)
- [GraphQL specification](https://spec.graphql.org/)
- [Protocol Buffers language guides](https://protobuf.dev/programming-guides/)
- [WSDL 2.0 Primer](https://www.w3.org/TR/wsdl20-primer/)

---

**Next**: [SOAP Overview](04_soap_overview.md)
