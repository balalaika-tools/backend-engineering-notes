# RESTful API Deep Dive

> A production-oriented path from REST constraints and HTTP semantics to secure evolution and operations.

[![HTTP](https://img.shields.io/badge/HTTP-RFC_9110-005C9C.svg)](https://www.rfc-editor.org/rfc/rfc9110)
[![OpenAPI](https://img.shields.io/badge/OpenAPI-3.x-6BA539.svg?logo=openapiinitiative&logoColor=white)](https://spec.openapis.org/oas/)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [01_rest_mental_model.md](01_rest_mental_model.md) | REST mental model | Constraints, resources, representations, state, and maturity |
| [02_http_semantics.md](02_http_semantics.md) | HTTP semantics | Methods, status codes, headers, negotiation, and intermediaries |
| [03_resource_and_uri_design.md](03_resource_and_uri_design.md) | Resource design | Boundaries, URIs, relationships, commands, and long-running work |
| [04_representations_validation_and_errors.md](04_representations_validation_and_errors.md) | Representations and errors | Media types, validation, Problem Details, and partial results |
| [05_pagination_filtering_and_search.md](05_pagination_filtering_and_search.md) | Collections | Cursor/offset pagination, filters, sorting, search, and consistency |
| [06_concurrency_idempotency_and_retries.md](06_concurrency_idempotency_and_retries.md) | Reliability | Preconditions, idempotency keys, ambiguous outcomes, and retry policy |
| [07_caching_and_performance.md](07_caching_and_performance.md) | Caching | Freshness, validators, cache keys, invalidation, and performance |
| [08_security.md](08_security.md) | Security | AuthN/AuthZ, BOLA, fields, CORS/CSRF, abuse, and SSRF |
| [09_versioning_compatibility_and_openapi.md](09_versioning_compatibility_and_openapi.md) | Evolution | Compatibility, versions, OpenAPI, deprecation, and migration |
| [10_testing_observability_and_operations.md](10_testing_observability_and_operations.md) | Operations | Test pyramid, telemetry, SLOs, rollout, and incident diagnosis |

---

## Reading Order

Read sequentially for a complete design path. For an existing API, start with the chapter matching the current risk and follow its cross-links.

---

## Prerequisites

- [API Fundamentals](../01_api_fundamentals.md)
- [HTTPX mental model](../../fundamentals/httpx/01_mental_model.md) for Python client transport details

