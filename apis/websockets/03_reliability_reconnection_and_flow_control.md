# WebSocket Reliability, Reconnection, and Flow Control

> **Who this is for**: Engineers who need WebSocket behavior to remain correct through slow clients, network loss, and reconnects. Assumes [Message Protocols and Contracts](02_message_protocols_and_contracts.md).

---

## 1. A Live TCP Connection Is Not a Delivery Guarantee

The server can successfully enqueue bytes locally and still lose them before the client processes the message.

```text
server application → server socket buffer → network → client socket buffer → client handler
       “send returned”                         connection dies here X
```

Decide what “delivered” means:

| Guarantee | Meaning | Application mechanism |
|-----------|---------|-----------------------|
| At-most-once | No retry; loss possible | Send and forget |
| At-least-once | Retry/replay until acknowledged; duplicates possible | Event ID, durable log, ack, deduplication |
| Effectively-once outcome | Duplicates may arrive but state change occurs once | Idempotent command/event processing |

“Exactly once” across a broken network normally hides a deduplication scope and retention window. State those limits instead of promising magic.

---

## 2. Liveness and Heartbeats

Connections can become half-open: each side believes it exists while the path no longer carries data.

RFC ping/pong frames detect protocol-path liveness. Many server libraries can send them automatically. The browser `WebSocket` API does not expose a method to originate protocol ping frames, so browser applications often use an application message:

```json
{"version":1,"type":"app.ping","message_id":"msg_1","sent_at":"2026-07-22T10:30:00Z","payload":{}}
```

```json
{"version":1,"type":"app.pong","message_id":"msg_2","request_id":"msg_1","sent_at":"2026-07-22T10:30:00Z","payload":{}}
```

Configure heartbeat interval shorter than the smallest known idle timeout in the proxy/load-balancer path, but not so frequent that heartbeat traffic dominates. Add tolerance for mobile suspension, event-loop delay, and temporary congestion.

Track:

- Last inbound frame/message
- Last heartbeat sent and pong observed
- Round-trip estimate if useful
- Consecutive missed intervals

Close stale connections and release their state. A heartbeat is not a substitute for domain acknowledgement.

---

## 3. Reconnection Policy

Clients should reconnect with capped exponential backoff and jitter:

```text
attempt 0: random(0, 0.5s)
attempt 1: random(0, 1s)
attempt 2: random(0, 2s)
...
cap:       30s
```

Immediate synchronized reconnect after a regional failure creates a **reconnect storm**. Servers should also enforce admission limits and may close with a temporary-overload code.

Stop or alter reconnection for:

- Explicit logout or user navigation
- Permanent authentication/authorization failure
- Unsupported protocol/client version
- Account suspension
- Repeated policy violations

Refresh credentials through the normal secure flow before reconnecting when they have expired. Do not loop forever with the same invalid token.

---

## 4. Resume and Catch-up

Reconnection restores a channel, not missed state. Choose one recovery model.

### Fresh snapshot

After reconnect, fetch current state over HTTP or a snapshot message. Simple and correct when intermediate events do not matter.

### Replay from an event position

```json
{
  "version": 1,
  "type": "order.subscribe",
  "message_id": "msg_9",
  "payload": {
    "order_id": "ord_123",
    "after_event_id": "evt_1042"
  }
}
```

The provider retains an ordered event log for a documented window and replies with events after the last processed ID.

### Gap detection plus snapshot

Events carry a monotonic stream sequence. If the client sees a gap or the resume position expired, it discards incremental assumptions and obtains a new snapshot.

```text
snapshot version 40
events 41, 42, 43
reconnect asks after 43
server oldest retained is 50 → resume_expired → fetch snapshot 55
```

Bind resume positions to principal, topic/filter, and schema version. An opaque cursor from another tenant must not grant data access.

---

## 5. Ordering and Deduplication

Messages on one WebSocket connection are observed in protocol order, but application processing can reorder them through concurrent tasks, brokers, shards, and reconnection.

Define ordering scope:

- Per connection
- Per user/topic/document/order
- Per partition
- No ordering guarantee

Use a stream sequence when order matters. Timestamps are poor sequence numbers because clocks differ and multiple events share timestamps.

For at-least-once delivery, retain processed event/message IDs or make operations state-assignment idempotent. Deduplication retention must cover maximum replay/retry age.

---

## 6. Backpressure and Slow Consumers

If the provider produces faster than one client can receive, an unbounded queue eventually exhausts memory.

```text
producer rate: 1,000 messages/s
client rate:     100 messages/s
backlog growth:  900 messages/s → failure is guaranteed without policy
```

Per-connection outbound queues must be bounded by messages and preferably bytes. Choose a policy by message meaning:

| Policy | Good for | Risk |
|--------|----------|------|
| Disconnect slow client | Durable/replayable streams | Reconnect load; needs resume |
| Drop oldest and send latest | Presence, cursor position, metrics snapshots | Intermediate transitions lost |
| Coalesce by key | State updates | More application logic |
| Reject producer command | Client-driven uploads/commands | Caller must handle backpressure |
| Spill to durable broker | Important events | Added latency/storage/complexity |

Do not silently drop financial or workflow events designed as a durable log.

In browsers, inspect `bufferedAmount` before continued sending; it reports application bytes queued for transmission. It does not provide an end-to-end acknowledgement. On the server, bound library queues and await sends or use a dedicated sender task so one slow socket cannot block global fan-out.

---

## 7. Command Timeouts and Cancellation

Every correlated command needs a deadline. On timeout:

1. Remove local pending state.
2. Decide whether to send an application cancellation.
3. Treat the server outcome as unknown if the command may have committed.
4. Query by operation/idempotency ID before repeating an unsafe command.

WebSocket disconnect does not roll back database work. Long operations should return a durable job ID early and expose status independently of the connection.

---

## 8. Reliability Test Matrix

- Network disappears without a close frame.
- Proxy drops idle connections.
- Client sleeps longer than heartbeat tolerance.
- Thousands of clients reconnect together.
- Resume token is current, expired, malformed, or from another tenant.
- Duplicate event and duplicate unsafe command arrive.
- Event gap occurs across broker partitions.
- Slow client fills its outbound queue.
- Server restarts while messages are in flight.
- Credential expires during a long connection.

For every case, assert cleanup, bounded memory, client-visible recovery, and durable state correctness.

---

## References

- [The WebSocket Protocol — RFC 6455](https://www.rfc-editor.org/rfc/rfc6455)
- [WHATWG WebSockets Standard: `bufferedAmount`](https://websockets.spec.whatwg.org/)

---

**Next**: [WebSocket Authentication and Security](04_authentication_and_security.md)

