# Webhook Testing, Observability, and Operations

> **Who this is for**: Teams operating webhook producers or consumers as a supported production integration.

---

## 1. Test the Exact Contract

Webhook fixtures should include **raw bytes plus headers**, not only parsed JSON:

```text
fixture/
├── invoice_paid.body.json          exact bytes used for signature
├── invoice_paid.headers.json       ID, timestamp, signature, content type
└── invoice_paid.expected.json      parsed normalized assertions
```

If a test loads JSON and serializes it again before verification, it misses the most common signature integration failure.

Validate every published example against its schema and run old fixtures through current consumer and producer code.

---

## 2. Producer Test Matrix

### Event creation

- Business transaction commits event atomically.
- Rollback emits no event.
- Relay crash/restart does not create duplicate logical deliveries.
- Subscription filters and payload versions select correctly.

### HTTP behavior

- Exact body matches signature.
- Explicit connect/write/read/pool timeouts apply.
- Success, each retryable class, terminal `4xx`, timeout, reset, huge response, and malformed response are classified.
- Redirects are blocked or fully revalidated.
- Response bodies/headers are capped and sanitized.

### Scheduling

- Backoff uses jitter and persists next attempt.
- Worker crash recovers an expired lease.
- Per-destination/tenant fairness holds under one broken endpoint.
- Attempts/age exhaustion reaches dead letter once.
- Replay keeps event identity and current security checks.

### SSRF

- All special/private IPv4 and IPv6 classes are blocked.
- Public DNS changing to private address is blocked on the next connection.
- Redirect to internal/metadata host fails.
- Egress network policy blocks what application validation misses.

---

## 3. Consumer Test Matrix

- Valid signature over exact bytes succeeds.
- One-byte body change, wrong secret, unsupported signature version, and malformed headers fail.
- Timestamp is too old, too far in the future, or valid at the boundary.
- Old/new secrets overlap during rotation.
- Duplicate event before, during, and after processing returns successful receipt but applies effect once.
- Provider re-signs same event with new timestamp; deduplication still uses event ID.
- Durable inbox unavailable causes non-success response.
- Unsupported event/schema quarantines without an infinite loop.
- Events arrive out of order or with a gap.
- Worker crashes before and after local transaction commit.
- Sensitive body/signature is absent from logs and errors.

Use property/fuzz tests for header parsing, JSON size/depth, event discriminators, and unexpected additional fields.

---

## 4. Integration and Failure Testing

Test across the real ingress/egress path because proxies affect bodies, headers, TLS, timeouts, and limits.

Inject:

- Slow DNS, connect, TLS, request read, and response write
- Connection closed before/after consumer commit
- `429` with extreme/malformed `Retry-After`
- Consumer outage followed by recovery and backlog surge
- Database/broker outage
- Clock skew
- Key rotation during retry backlog
- Provider/consumer deployment with mixed schema versions

Local forwarding tunnels are useful for development but change TLS, source IP, DNS, and network path. They do not prove production security or timing behavior.

Provide a sandbox/test-event generator that uses the exact production signer and envelope code, with clearly non-production secrets and data.

---

## 5. Producer Observability

Metrics with bounded labels:

- Events and deliveries created by event type/version
- Attempts and outcomes by status class/failure category
- Delivery latency from `occurred_at` to accepted
- Queue due depth, oldest age, worker lease recovery
- Retry amplification and dead-letter count
- Endpoint pause/disable and replay jobs
- Egress DNS/connect/TLS/request duration

Do not label metrics by endpoint URL, tenant, event ID, delivery ID, or raw status text. Put those in access-controlled logs/traces.

Logs correlate `event_id`, `delivery_id`, `attempt_id`, subscription/tenant, safe destination host, resolved destination classification, status/failure code, duration, and next action. Redact query strings, secrets, signature headers, and response bodies.

---

## 6. Consumer Observability

Track:

- Requests accepted/rejected by bounded reason
- Signature/timestamp/schema failures
- New vs duplicate inbox inserts
- Inbound lag: `received_at - occurred_at`
- Inbox state, due depth, oldest age, processing attempts
- Processing success/failure/quarantine by event type
- Reconciliation repairs and checkpoint lag

Alert on symptoms:

- No events when normal traffic is expected
- Signature failures after key/provider change
- Oldest inbox/delivery age breaching target
- Dead-letter/quarantine growth
- Duplicate rate spike
- Reconciliation drift

An HTTP `2xx` dashboard alone can be green while the inbox worker is broken.

---

## 7. SLOs and Retention

Producer SLO examples:

- Percentage of eligible events accepted by healthy endpoints within 1/5/30 minutes
- Platform-caused delivery failure rate
- Replay API/control-plane availability

Consumer SLO examples:

- Percentage of valid events durably accepted within endpoint deadline
- Percentage processed within target time
- Inbox/reconciliation freshness

Separate unhealthy customer endpoints from producer platform failure without hiding fleet saturation caused by those endpoints.

Define retention for:

- Immutable event bodies
- Delivery/attempt metadata
- Dead letters
- Consumer inbox and deduplication keys
- Replay capability
- Audit/security events

Retention must satisfy replay/idempotency needs and data minimization. Encrypt sensitive payloads and restrict support access.

---

## 8. Operational Tooling

A useful producer console/API shows:

- Endpoint status and selected event/version
- Secret rotation state without revealing stored secret
- Recent deliveries with event/delivery/attempt IDs
- Bounded request/response diagnostics
- Next retry and failure classification
- Test delivery, pause/resume, and authorized replay
- Documentation link and support correlation ID

A consumer console/runbook shows inbox/quarantine state, processing attempts, schema version, safe payload metadata, reprocess action, and authoritative reconciliation status.

All manual actions require authorization, audit records, rate limits, and preview for bulk scope.

---

## 9. Incident Runbooks

### Producer backlog

```text
Is event creation current?
  → scheduler/lease issue, worker capacity, DNS/egress, or destination cohort?
  → pause pathological destination, add controlled capacity, preserve fairness
  → drain with rate limits, monitor oldest age
```

### Consumer stopped processing

```text
Is ingress accepting and inbox growing?
  ├── no → TLS/DNS/signature/body/database ingress path
  └── yes → worker lease, dependency, poison event, database contention
             → quarantine/fix/reprocess → reconcile authoritative state
```

### Signing secret exposure

Rotate, accept overlap only as necessary, revoke old key, inspect failed/suspicious deliveries, and deduplicate/reconcile events. Do not delete evidence or rotate without updating retry backlog signing behavior.

---

## 10. Production Readiness Checklist

- Raw signed fixtures and schemas are versioned and validated.
- Transactional outbox/inbox and duplicate/race tests pass.
- SSRF is blocked at URL, resolution/connection, redirect, and network layers.
- Backlog, retry storm, crash, and recovery tests demonstrate bounded behavior.
- Metrics cover delivery and processing lag, not only HTTP status.
- Owners have safe delivery/quarantine/replay tooling and runbooks.
- Replay, deduplication, event, and audit retention agree.
- Reconciliation proves eventual correctness beyond webhook delivery.

---

## References

- [GitHub webhook testing and troubleshooting](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks)
- [GitHub webhook best practices](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)
- [Stripe webhook documentation](https://docs.stripe.com/webhooks)

---

**Next**: Return to the [API Communication Guides](../README.md) or apply the patterns in the [FastAPI Guides](../../fundamentals/fastapi/README.md).

