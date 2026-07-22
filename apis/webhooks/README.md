# Webhook Deep Dive

> Reliable and secure event delivery across independently operated HTTP systems.

[![HTTP](https://img.shields.io/badge/HTTP-Webhooks-005C9C.svg)](https://www.rfc-editor.org/rfc/rfc9110)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)

---

## Contents

| File | Topic | Description |
|------|-------|-------------|
| [01_delivery_model_and_event_contracts.md](01_delivery_model_and_event_contracts.md) | Delivery model | Webhook roles, event envelopes, lifecycle, guarantees, and reconciliation |
| [02_producer_design.md](02_producer_design.md) | Producer | Subscriptions, outbox, delivery state, retry queues, replay, and endpoints |
| [03_consumer_design.md](03_consumer_design.md) | Consumer | Raw-body verification, durable inbox, quick acknowledgement, deduplication |
| [04_signatures_security_and_ssrf.md](04_signatures_security_and_ssrf.md) | Security | HMAC, replay defense, rotation, endpoint ownership, SSRF, and egress isolation |
| [05_retries_idempotency_ordering_and_replay.md](05_retries_idempotency_ordering_and_replay.md) | Reliability | Failure classes, backoff, duplicates, ordering, replay, and reconciliation |
| [06_testing_observability_and_operations.md](06_testing_observability_and_operations.md) | Operations | Fixtures, failure tests, metrics, dashboards, runbooks, and support tooling |

---

## Reading Order

Read sequentially. Producer and consumer responsibilities are deliberately separated before the shared security and delivery semantics chapters.

---

## Prerequisites

- [API Fundamentals](../01_api_fundamentals.md)
- [Safe and Scalable API Calls](../../fundamentals/fastapi/safe_and_scalable_api_calls/README.md) for outbound HTTP resilience

