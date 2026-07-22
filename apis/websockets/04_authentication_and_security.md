# WebSocket Authentication and Security

> **Who this is for**: Engineers securing browser and service WebSocket channels from handshake through every message. Assumes [Protocol and Connection Lifecycle](01_protocol_and_connection_lifecycle.md).

---

## 1. Security Spans the Connection Lifetime

An HTTP middleware check at connection time is not enough.

```text
TLS and trusted edge
  → validate Origin and subprotocol
  → authenticate connection
  → apply connection/admission limits
  → authorize every subscription/command/object
  → validate and budget every message
  → refresh/revoke identity during long lifetime
  → redact telemetry and clean up on close
```

WebSocket messages do not carry ordinary HTTP status codes and headers after the handshake. Design application errors and close policy explicitly.

---

## 2. Always Use `wss://` in Production

TLS protects credentials, message confidentiality/integrity, and the server identity. Terminating TLS at a load balancer is common, but the internal hop still needs an explicit trust boundary. Use TLS or authenticated private networking between edge and application where the threat model requires it.

Configure:

- Modern TLS policy and certificate rotation
- Trusted proxy addresses before accepting forwarded client/proto fields
- Secure cookies with appropriate `Secure`, `HttpOnly`, and `SameSite` attributes
- No mixed-content downgrade from an HTTPS page to `ws://`

Masking in RFC 6455 is not encryption.

---

## 3. Authentication Options

Browser JavaScript's native `WebSocket` constructor accepts a URL and optional subprotocols; it does not provide a general way to add an arbitrary `Authorization` header.

| Mechanism | Fit | Main risk/control |
|-----------|-----|-------------------|
| Secure same-site cookie | Browser app sharing auth origin | Cross-site WebSocket hijacking; require exact `Origin` validation and CSRF-conscious cookie policy |
| Short-lived single-use connection ticket | Cross-origin browser/app gateway | URL/query may be logged; make ticket scoped, one-use, very short-lived, and scrub URLs |
| First application auth message | Browser or custom client | Unauthenticated socket consumes resources; enforce tiny pre-auth limit and short timeout |
| `Authorization` during handshake | Non-browser/native clients | Supported by client/library, but still validate issuer/audience/expiry |
| Mutual TLS | Controlled service/device clients | Certificate issuance, identity mapping, rotation, and revocation operations |

Do not place a long-lived bearer token in the query string. URLs appear in access logs, analytics, browser history, proxy diagnostics, and support screenshots.

Avoid inventing a convention that smuggles secrets through `Sec-WebSocket-Protocol`; subprotocol values are for negotiation and may be logged or echoed. Use a reviewed scheme if ecosystem constraints force it.

---

## 4. Origin Validation and Cross-Site Hijacking

A browser can initiate a WebSocket from a malicious site and may attach cookies for the target domain. If the server authenticates only by cookie and accepts any origin, the malicious page can act through the victim's session: **Cross-Site WebSocket Hijacking**.

Validate the handshake's `Origin` against an exact allowlist:

```text
✅ https://app.example.com
✅ https://admin.example.com
❌ https://app.example.com.attacker.test
❌ null (unless a narrowly reviewed client requires it)
❌ absent for a browser-only endpoint
```

Parse origins as origins; do not use substring or suffix checks. Define a separate policy for non-browser clients, because they can set or omit `Origin` themselves.

CORS response headers do not protect WebSocket handshakes in the same way they govern Fetch/XHR. Enforce origin policy in WebSocket handling and at a compatible trusted edge.

Origin proves browser page origin, not user identity. Non-browser attackers can forge it.

---

## 5. Authorization Is Per Message and Resource

Binding `user_id` to a connection does not authorize every topic.

```text
subscribe document doc_123
  → connection is authenticated
  → principal has document:read
  → document belongs to tenant
  → membership grants current access
  → subscription count/cost allowed
```

Apply the same object/function/property controls as HTTP APIs:

- Scope database/broker lookups to the principal's tenant.
- Recheck permissions for sensitive commands.
- Filter outbound fields; channel membership is not permission to see all internal state.
- Do not trust tenant/user IDs inside messages.
- Remove subscriptions promptly when membership is revoked.

If authorization may change during a long connection, choose one:

- Short connection/credential lifetime and forced reauthentication
- Revocation events that disconnect affected sessions
- Re-evaluation on each sensitive action
- A policy cache with bounded TTL and invalidation

Document the maximum revocation delay.

---

## 6. Message and Connection Abuse Controls

Enforce limits at several levels:

| Surface | Controls |
|---------|----------|
| Handshake | Rate, concurrent connections per principal/IP/tenant, auth timeout |
| Frame/message | Compressed and decompressed size, UTF-8/JSON validity, nesting |
| Traffic | Messages/bytes per second, burst, inbound command concurrency |
| Subscription | Count, topic authorization, filter complexity, fan-out cost |
| Outbound | Bounded queue and slow-consumer policy |
| Lifetime | Idle timeout, credential expiry, maximum age if required |

Compression can turn a small wire frame into a large in-memory message. Limit after decompression and tune library compression context settings.

Reject binary messages on a JSON-only protocol and unknown subprotocol versions. Close repeated invalid traffic with a policy-appropriate code; do not spend unbounded CPU constructing errors for an attacker.

---

## 7. Input, Output, and Injection

- Parse into a discriminated schema before dispatch.
- Bind SQL values and allowlist message types, topics, filters, and sort fields.
- Escape/sanitize at the final browser rendering context; a trusted WebSocket transport does not make event strings safe HTML.
- Never let client messages choose internal broker subjects or Redis keys directly.
- Do not deserialize language-native object formats from untrusted peers.
- Validate outbound events too; one malformed event should not corrupt every connected client.

A broker message is also untrusted at the edge. Compromised internal publishers must not bypass per-connection field filtering or tenant routing.

---

## 8. Secrets and Telemetry

Log:

- Connection ID, principal/tenant ID, origin, negotiated protocol
- Accepted/rejected reason code
- Message type and bounded size, not body by default
- Subscription count, queue depth, close code, duration
- Request/message/event IDs for correlation

Do not log cookies, tickets, bearer tokens, raw query strings, message bodies containing user content, or close reasons supplied by an untrusted peer without sanitization.

Metrics must use bounded labels. Connection ID, user ID, topic ID, and raw close reason belong in logs/traces, not metric labels.

---

## 9. Security Test Matrix

- Wrong/absent/malformed origin for browser endpoint
- Valid cookie from a disallowed origin
- Expired, revoked, wrong-audience, and reused one-time ticket
- Message before first-message authentication
- Subscription to another tenant's object
- Permission revoked while subscribed
- Oversized fragmented/compressed message
- Flood of invalid JSON, pings, commands, or subscriptions
- Slow reader filling outbound queue
- Script payload rendered by browser client
- Secret absent from access logs, errors, traces, and close reasons

---

## References

- [The WebSocket Protocol: Security Considerations — RFC 6455](https://www.rfc-editor.org/rfc/rfc6455)
- [WHATWG WebSockets Standard](https://websockets.spec.whatwg.org/)
- [OWASP WebSocket Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html)

---

**Next**: [Scaling and Distributed Architecture](05_scaling_and_distributed_architecture.md)

