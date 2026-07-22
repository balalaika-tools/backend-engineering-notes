# Testing, Observability, and Operations

> **Who this is for**: Teams taking a RESTful API from correct local behavior to a dependable production service. Assumes the preceding REST chapters.

---

## 1. Test the Contract, Not Only Functions

A useful test portfolio covers different failure boundaries:

| Layer | Proves | Example |
|-------|--------|---------|
| Unit | Domain rule in isolation | Cancellation allowed only while pending |
| Schema | Documents/examples match types | Every OpenAPI example validates |
| Endpoint/component | Routing, validation, auth, error mapping | Cross-tenant ID returns scoped `404` |
| Database integration | Transactions, constraints, query behavior | Two conditional updates cannot both win |
| Consumer/provider contract | Deployed sides honor agreed examples | Old client decodes new additive response |
| End-to-end | Gateway, TLS, identity, service, dependencies | Real token reaches staging operation |
| Property/fuzz | Parser and invariant behavior over generated input | Unknown JSON shapes never cause `500` |
| Load/resilience | Limits and failure recovery | Dependency slowdown triggers admission control |

More end-to-end tests are not automatically better. Keep the majority deterministic and close to the behavior, then use a smaller set to prove infrastructure wiring.

---

## 2. Endpoint Test Matrix

For each operation cover:

- Minimum valid and representative full request
- Malformed encoding and wrong media type
- Missing, unknown, boundary, null, and oversized fields
- Missing/invalid/expired/wrong-audience credential
- Missing function capability
- Same-tenant allowed and cross-tenant denied objects
- Allowed and forbidden property updates
- Current-state conflict and stale `If-Match`
- Duplicate idempotency key with same and different body
- Dependency timeout, unavailable, malformed response, and partial failure
- Stable error type/code and no sensitive detail
- Cache and rate-limit headers where applicable

Authorization tests should build at least two tenants by default so the unsafe query is easy to catch.

---

## 3. Contract Assertion Example

```python
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_unknown_order_uses_problem_details(auth_headers: dict[str, str]) -> None:
    response = client.get("/orders/ord_missing", headers=auth_headers)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "https://api.example.com/problems/order-not-found",
        "title": "Order not found",
        "status": 404,
        "detail": "No visible order has that identifier.",
        "instance": "/orders/ord_missing",
        "code": "ORDER_NOT_FOUND",
        "request_id": response.json()["request_id"],
    }


def test_openapi_has_stable_operation_ids() -> None:
    schema = client.get("/openapi.json").json()
    operation_ids = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]

    assert len(operation_ids) == len(set(operation_ids))
    assert all(" " not in operation_id for operation_id in operation_ids)
```

Avoid asserting volatile timestamps or entire generated schemas in every test. Assert stable contract decisions and use a deliberate schema snapshot/diff workflow for the full document.

---

## 4. Observe the Request Lifecycle

Use the **RED** signals per stable operation:

- Rate: accepted and rejected requests
- Errors: status class plus stable problem code
- Duration: latency distribution, not only average

Add saturation and dependency signals:

- In-flight requests and admission rejections
- Worker/event-loop lag, threads, CPU, memory
- Database pool wait and query latency
- Outbound pool wait, connect, TLS, and response time
- Cache hit/miss/validation rate
- Retry attempts and amplification

Label metrics with normalized route/operation ID, method, status class, and bounded error code. Never label with raw `/orders/ord_123`, user ID, request ID, or arbitrary exception text; high cardinality can make telemetry itself fail.

---

## 5. Correlation and Tracing

Use distinct identifiers:

| Identifier | Scope |
|------------|-------|
| Request ID | One inbound API request and its logs |
| Trace ID | Distributed call graph across services |
| Idempotency key | Logical retryable operation |
| Resource/job/event ID | Durable business entity |

Propagate standard trace context through trusted services and return a safe request ID for support. Do not let an arbitrary external request ID overwrite internal uniqueness; validate or generate your own and preserve the external value separately if useful.

Trace spans should show queue wait, auth, database, outbound dependencies, serialization, and retries. Sampling must retain enough errors and slow traces for diagnosis without collecting sensitive bodies.

---

## 6. SLOs and Alerts

Define service-level indicators from the consumer's outcome:

- Availability: valid requests that receive an acceptable response
- Latency: successful or all requests within a threshold
- Correctness/freshness where technically measurable

Exclude only traffic the contract clearly defines as invalid. A provider-caused `400` due to a breaking schema rollout is not magically healthy.

Alert on symptoms and budget burn rather than every individual failure:

- Fast/slow error-budget burn
- Sustained tail-latency increase
- Admission rejection or queue growth
- Dependency failure and retry amplification
- Sudden change in authorization denials or problem codes

---

## 7. Safe Rollout and Shutdown

Before deployment:

- Run compatibility diff and migration tests.
- Ensure database changes follow expand/migrate/contract.
- Verify gateway timeouts and limits match the contract.
- Canary by a controllable slice and compare errors/latency.
- Confirm rollback can read data written by the new version.

During shutdown:

1. Fail readiness so new requests stop arriving.
2. Drain in-flight requests within a bounded grace period.
3. Stop accepting background work and persist unfinished state.
4. Close pools and flush telemetry within their own deadlines.

Killing workers mid-request increases ambiguous outcomes. Idempotency and reconciliation remain necessary because no drain is perfect.

---

## 8. Incident Diagnosis Path

```text
Is impact real for consumers?
  → which operation, version, tenant cohort, and region?
  → edge rejection, queueing, application, database, or dependency?
  → did retries amplify it?
  → can load be shed, feature disabled, or rollout reverted safely?
  → how are ambiguous writes reconciled afterward?
```

Useful runbook data:

- Current deploy and schema versions
- Dashboards by operation/problem code
- Logs/traces searchable by request and resource ID
- Dependency and database health
- Feature flags and rollback constraints
- Retry/idempotency/reconciliation procedure
- Owner and escalation contacts

---

## 9. Production Readiness Checklist

- Contract, examples, and implementation pass conformance checks.
- Negative, authorization, concurrency, and compatibility tests exist.
- Load test includes realistic dependency and database behavior.
- Metrics use bounded dimensions and trace the full critical path.
- SLO, dashboards, alerts, owner, and runbook are published.
- Deployment drains safely and rollback is data-compatible.
- Unknown write outcomes can be queried or reconciled.
- Logs and traces are useful without leaking sensitive content.

---

**Next**: [WebSocket Deep Dive](../websockets/README.md)

