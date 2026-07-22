# WebSocket Message Protocols and Contracts

> **Who this is for**: Engineers designing the application protocol that runs inside a WebSocket channel. Assumes [Protocol and Connection Lifecycle](01_protocol_and_connection_lifecycle.md).

---

## 1. WebSocket Gives You a Channel, Not an API

RFC 6455 defines how text/binary messages cross a connection. It does not define what this means:

```json
{"action":"update","data":{"x":1}}
```

A production contract must define message categories, schemas, correlation, permissions, ordering, acknowledgements, errors, limits, and evolution.

```text
WebSocket protocol: framing, ping/pong, close
application protocol: authenticate, subscribe, command, event, reply, error, ack
domain rules: who may edit which document and when
```

Keep these layers separate so protocol control is not confused with domain acknowledgements.

---

## 2. A Versioned Envelope

Use a stable envelope that routes and correlates messages before parsing type-specific payloads:

```json
{
  "version": 1,
  "type": "order.subscribe",
  "message_id": "msg_01J8Q...",
  "request_id": "req_01J8Q...",
  "sent_at": "2026-07-22T10:30:00Z",
  "payload": {
    "order_id": "ord_123",
    "after_event_id": "evt_987"
  }
}
```

| Field | Purpose |
|-------|---------|
| `version` | Envelope/schema compatibility |
| `type` | Stable routing discriminator |
| `message_id` | Identity for logs, deduplication, or acknowledgement |
| `request_id` | Correlates a command with its reply |
| `sent_at` | Diagnostic/event time; not ordering by itself |
| `payload` | Type-specific content |

Do not put authorization decisions or trusted user/tenant identity in client-controlled envelope fields. Bind them to the authenticated connection context.

---

## 3. Message Categories

| Category | Direction | Example | Expected response |
|----------|-----------|---------|-------------------|
| Command | Client → server | `order.cancel` | Reply or error correlated by `request_id` |
| Query | Client → server | `document.snapshot.get` | One reply; consider HTTP if ordinary request-response |
| Subscribe | Client → server | `order.subscribe` | Subscription confirmation/error |
| Event | Server → client | `order.status_changed` | Optional delivery/application acknowledgement |
| Ack | Either | `event.ack` | Usually none |
| Protocol message | Either | `app.ping`, `app.pong` | Defined by application, distinct from RFC ping/pong |
| Error | Server → client | `error` | Correlated if caused by a request |

Do not emit an unstructured error string:

```json
{
  "version": 1,
  "type": "error",
  "request_id": "req_123",
  "message_id": "msg_456",
  "payload": {
    "code": "ORDER_NOT_CANCELLABLE",
    "message": "Only pending orders can be cancelled.",
    "retryable": false
  }
}
```

The stable `code` drives client behavior. The message is safe human text. Internal exceptions stay in server telemetry.

---

## 4. Define State Machines

A message schema alone cannot describe which messages are legal now.

```text
connection
UNAUTHENTICATED ──authenticate──> READY ──subscribe──> SUBSCRIBED
       │                            │                      │
       └──────── reject ────────────┴──── unsubscribe ─────┘
```

For each state define:

- Allowed incoming message types
- Required authentication/authorization context
- Server responses and timeouts
- Maximum outstanding requests/subscriptions
- Behavior for duplicate commands
- Close code for protocol/policy violations

Reject a validly shaped `document.edit` if the connection has not subscribed or the principal lacks edit permission. Structural validation is only the first gate.

---

## 5. Correlation and Multiplexing

One connection may carry many logical requests and subscriptions. Use IDs rather than relying on response order:

```text
client: request A ───────────────>
client: request B ───────────────>
server: reply B  <───────────────
server: event X  <───────────────
server: reply A  <───────────────
```

Maintain a bounded map of outstanding requests with deadlines. Delete entries on reply, error, timeout, and disconnect. An unbounded correlation map is a memory leak and abuse surface.

Use a separate `subscription_id` if the same topic can be subscribed with different filters. Do not overload connection ID, user ID, request ID, and event ID.

---

## 6. Validation and Limits

Validate in this order:

1. Message byte limit after decompression.
2. UTF-8/JSON or binary decoding.
3. Envelope schema and known version.
4. Known message type.
5. Type-specific payload schema.
6. Connection state and authorization.
7. Domain state and concurrency.
8. Rate/cost/concurrency limits.

Limit:

- Message bytes and nesting depth
- Message rate and burst
- Concurrent commands awaiting replies
- Active subscriptions and filter complexity
- Outbound queue bytes/messages per connection
- Total connection lifetime or credential age where policy requires it

Invalid client data normally produces a bounded application error. Repeated malformed or policy-violating traffic should close the connection rather than create an infinite error loop.

---

## 7. Evolution

Compatible evolution patterns:

- Add optional payload fields.
- Add a new message type that old clients never receive unless they opt in.
- Add capabilities during connection setup or a `capabilities` message.
- Preserve unknown fields when proxying only if the contract requires it.

Breaking changes:

- Rename/remove/change a required field.
- Change units, meaning, ordering, or acknowledgement semantics.
- Start sending a new event type to clients that assume an exhaustive set.
- Change whether a command can be repeated safely.

Options for major change:

- Negotiate a new `Sec-WebSocket-Protocol` value such as `orders.v2`.
- Version every envelope and run both decoders.
- Expose a new endpoint during migration.

Measure active client versions and define a forced-upgrade policy. Long-lived connections may retain an old contract after a deployment until they reconnect.

---

## 8. Contract Documentation

For each message type document:

- Direction and allowed connection state
- Authentication/capability requirement
- JSON/binary schema and realistic example
- Correlation and acknowledgement rules
- Side effects and idempotency
- Ordering and delivery guarantee
- Rate, size, timeout, and retention limits
- Error codes and retry behavior
- Added/deprecated versions

AsyncAPI can describe channels and messages, but application state machines and recovery rules still need explicit prose and tests.

---

**Next**: [Reliability, Reconnection, and Flow Control](03_reliability_reconnection_and_flow_control.md)

