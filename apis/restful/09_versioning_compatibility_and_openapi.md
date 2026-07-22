# Versioning, Compatibility, and OpenAPI

> **Who this is for**: API owners evolving HTTP contracts across independently deployed consumers. Assumes [API Contracts and Lifecycle](../03_api_contracts_and_lifecycle.md).

---

## 1. Prefer Evolution Over Proliferation

Most changes should be backward-compatible within the existing API:

```text
safe additive change → deploy provider → consumers adopt when ready

breaking change → create migration boundary → overlap old/new → measure → retire old
```

A new major version duplicates documentation, security review, routing, tests, support, and fixes. Use one when the semantics cannot coexist, not for every release.

---

## 2. Change Classification

| Change | Structural compatibility | Behavioral risk |
|--------|--------------------------|-----------------|
| Add optional response field | Usually compatible | Strict clients may reject unknowns |
| Add optional request field | Usually compatible | Intermediaries may strip it |
| Add new endpoint/method | Compatible | New permissions/inventory required |
| Add enum value | Shape-compatible | Exhaustive clients can fail |
| Increase max page size | Compatible | Client memory/latency assumptions change |
| Tighten validation | Breaking for accepted traffic | Often missed by schema diff |
| Change default sort | Shape-compatible | Pagination and UI behavior break |
| Make field required | Breaking | Old payloads omit it |
| Rename/remove/change type | Breaking | Direct contract failure |
| Change units/meaning | Wire-compatible | Potential data corruption |
| Change error/status/retry behavior | Behaviorally breaking | Can cause retry storms or wrong UX |

Compatibility review must include schema, semantics, operational behavior, and security.

---

## 3. Versioning Mechanisms

### URI path

```text
GET /v2/orders/ord_123
```

Visible, routeable, and easy to test. It can fragment links and encourage copying the entire API when only one area changed.

### Media type or request header

```http
Accept: application/vnd.example.orders+json; version=2
```

Keeps resource paths stable but is harder to explore and must participate in cache selection.

### Date/revision

```http
API-Version: 2026-07-01
```

Lets providers define behavior snapshots, but many active revisions can create a test matrix. Document whether the version is account-pinned, request-selected, or endpoint-specific.

Choose one strategy across the product. Version discovery, defaults, errors, cache behavior, documentation, SDK selection, and support window are part of the strategy.

---

## 4. OpenAPI as an Executable Contract

OpenAPI describes HTTP operations and their inputs, outputs, and security schemes. The latest published specification is currently 3.2.0, while 3.1 and 3.0 remain common in tooling. Select the newest revision that every generator, gateway, validator, and documentation tool in your pipeline supports correctly.

```yaml
openapi: 3.1.0
info:
  title: Orders API
  version: 1.4.0
paths:
  /orders/{order_id}:
    get:
      operationId: getOrder
      summary: Retrieve an order visible to the caller
      parameters:
        - name: order_id
          in: path
          required: true
          schema:
            type: string
            pattern: '^ord_[A-Za-z0-9]+$'
      responses:
        '200':
          description: Current order representation
          headers:
            ETag:
              schema: {type: string}
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Order'
        '404':
          $ref: '#/components/responses/NotFound'
      security:
        - oauth2: [order:read]
components:
  schemas:
    Order:
      type: object
      additionalProperties: false
      required: [id, status, amount_minor, currency]
      properties:
        id: {type: string}
        status:
          type: string
          enum: [pending, paid, cancelled]
        amount_minor: {type: integer, minimum: 0}
        currency: {type: string, pattern: '^[A-Z]{3}$'}
```

`info.version` describes the document/API release; it is not the OpenAPI Specification version and does not by itself route clients to `/v1` or `/v2`.

---

## 5. What OpenAPI Does Not Guarantee

OpenAPI can express shapes and many constraints, but not all behavior:

- Object-level authorization
- Idempotency key storage/replay semantics
- Database transaction boundaries
- Ordering across pages
- Exact rate, retry, and consistency guarantees
- Which errors are safe to retry
- Cross-field business invariants in every case
- Deprecation dates and migration completion by itself

Add prose and executable tests for these. Do not assume generated SDKs supply safe timeouts, retry rules, credential storage, or pagination iterators unless inspected and tested.

---

## 6. Contract Pipeline

```text
edit OpenAPI
  → validate syntax and references
  → lint organization rules
  → diff against released contract
  → validate examples
  → generate docs/types/clients
  → provider conformance tests
  → consumer contract tests
  → publish immutable artifact
```

Breaking-change tools find structural differences. Maintain policy tests for behavioral constraints such as default limits, response headers, error types, authentication, and unknown fields.

Check generated server output into source control only when the team deliberately reviews and versions it. Otherwise generate reproducibly in the build from a pinned toolchain.

---

## 7. Deprecation and Migration

Good deprecation includes:

- Deprecated operation/field and replacement
- Reason and migration example
- Announcement date, support end, and earliest removal date
- Affected versions/SDKs
- Consumer-specific usage telemetry
- Contact and exception process

```text
announce → stop new adoption → migrate active consumers
         → warn remaining callers → verify zero/accepted use → retire
```

Do not remove based only on aggregate traffic. One low-volume finance or disaster-recovery consumer may be critical. Identify principal, operation, and version without recording sensitive payloads.

---

## 8. Compatibility Testing

Maintain fixtures for:

- Old request against new provider
- New response decoded by representative old clients
- Unknown fields and enum values
- Old/new pagination cursors during rollout
- Old idempotency records and replayed responses
- Deprecated operation warnings
- Mixed provider versions behind a load balancer
- Rollback after the new version has written data

Backward-compatible reads are insufficient if new writes produce state the old provider cannot process after rollback.

---

## References

- [OpenAPI Specification](https://spec.openapis.org/oas/)
- [OpenAPI Specification 3.2.0](https://spec.openapis.org/oas/v3.2.0.html)

---

**Next**: [Testing, Observability, and Operations](10_testing_observability_and_operations.md)

