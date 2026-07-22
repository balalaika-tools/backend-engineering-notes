# WebSocket Protocol and Connection Lifecycle

> **Who this is for**: Backend practitioners who know HTTP request-response and need an accurate model of persistent bidirectional connections. Start with [API Fundamentals](../01_api_fundamentals.md).

---

## 1️⃣ What Problem WebSocket Solves

The **WebSocket Protocol** provides a persistent, full-duplex channel in which either peer can send application messages independently.

```text
HTTP request-response
client ──request──> server
client <─response── server

WebSocket
client ──opening handshake──> server
client <════ messages both ways ════> server
client <──closing handshake────────> server
```

After the opening handshake, repeated application messages avoid an HTTP request header block per message. The trade-off is connection state: capacity, authentication expiry, liveness, reconnection, deployment draining, and recovery become application concerns.

Use WebSocket for frequent, low-latency, bidirectional traffic such as collaboration, multiplayer state, interactive agents, or chat. Do not use it merely because “real time” sounds desirable; polling or Server-Sent Events (SSE) is simpler for many one-way feeds.

---

## 2️⃣ Opening Handshake

For HTTP/1.1, a WebSocket begins with an Upgrade request:

```http
GET /realtime HTTP/1.1
Host: api.example.com
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==
Sec-WebSocket-Version: 13
Origin: https://app.example.com
Sec-WebSocket-Protocol: orders.v1
```

```http
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=
Sec-WebSocket-Protocol: orders.v1
```

The key/accept calculation proves the server understood a WebSocket handshake; it is not authentication or encryption. Use `wss://` so TLS protects the handshake and messages.

WebSockets can also be bootstrapped over HTTP/2 and HTTP/3 using Extended CONNECT mechanisms. Support is a property of the entire client, proxy, load balancer, and server path—do not assume that enabling HTTP/2 on an edge automatically moves existing WebSocket connections to it.

### Before acceptance

The server should validate:

- Target path and negotiated subprotocol
- Authentication material available during the handshake
- `Origin` for browser connections
- Tenant/account limits and maintenance/admission policy

Once accepted, ordinary HTTP responses and CORS processing are no longer the application error mechanism. Rejections and post-accept failures follow framework and WebSocket close semantics.

---

## 3️⃣ Messages and Frames

Applications send **messages**. On the wire, a message consists of one or more **frames**.

```text
application text message:  "large document update ..."
wire frames:               TEXT(fin=0) → CONTINUATION(fin=0) → CONTINUATION(fin=1)
```

Frame categories:

| Kind | Meaning |
|------|---------|
| Text | UTF-8 application data |
| Binary | Arbitrary application bytes |
| Continuation | Remaining fragments of a data message |
| Ping | Protocol liveness/control probe |
| Pong | Reply to ping or unsolicited heartbeat |
| Close | Starts closing handshake with optional code/reason |

Control frames are limited in size and cannot be fragmented. They may appear between fragments of a data message, so libraries must process them promptly rather than waiting for one giant message to finish.

Clients mask frames sent to servers; servers do not mask frames sent to clients. Masking protects intermediaries from particular cache/proxy attacks; it is not confidentiality. TLS supplies confidentiality and peer/server authentication.

> **Key insight**: WebSocket preserves application message boundaries, unlike a raw TCP byte stream. Fragment boundaries are transport details and must not be treated as application records.

---

## 4️⃣ Connection State

```text
CONNECTING → OPEN → CLOSING → CLOSED
               │        ↑
               └─ error/network loss ──> abnormal closure
```

At `OPEN`, both peers can send. At `CLOSING`, no new application work should be admitted. At `CLOSED`, the application releases connection registries, subscriptions, queues, and presence leases.

A connection can disappear without a close frame because a device sleeps, NAT mapping expires, process crashes, cable disconnects, or proxy times out. Cleanup must run on both graceful and exceptional exits, and presence cannot depend on a perfect disconnect signal.

---

## 5️⃣ Closing Handshake and Codes

A peer sends a close frame; the other normally echoes a close and both close the underlying connection.

| Code | Typical use |
|------|-------------|
| `1000` | Normal completion |
| `1001` | Endpoint going away, such as deploy or navigation |
| `1002` | Protocol error |
| `1003` | Unsupported data type |
| `1007` | Invalid payload data, such as invalid UTF-8 |
| `1008` | Application policy violation |
| `1009` | Message too large |
| `1011` | Unexpected server condition |
| `1012` | Service restart |
| `1013` | Temporary overload; try again later |

Some code values are reserved and must not be placed in a close frame. In particular, `1006` is a local indication that closure was abnormal; an endpoint does not send a `1006` close frame.

Close reasons are short UTF-8 diagnostics, not secure error channels. Do not include tokens, stack traces, internal hostnames, or sensitive business data.

---

## 6️⃣ Subprotocol and Extensions

`Sec-WebSocket-Protocol` negotiates an application subprotocol such as `orders.v1`. The server must choose one value offered by the client; it must not invent an unoffered protocol.

Subprotocol negotiation is useful when message semantics differ incompatibly. A version in every application message can instead support gradual evolution on one channel. Pick one clear strategy.

Extensions can alter protocol operation. Compression such as per-message deflate reduces transfer size but increases CPU, memory, and decompression-abuse risk. Apply message limits to decompressed content and test library/proxy behavior before enabling it broadly.

---

## 7️⃣ Alternatives

| Requirement | Prefer | Why |
|-------------|--------|-----|
| Ordinary query/command | HTTP request-response | Stateless, observable, cacheable where appropriate |
| Infrequent change check | Polling with validators | Simplest recovery and infrastructure |
| One delayed HTTP response until change | Long polling | Broad compatibility, but repeated requests |
| One-way browser event feed | SSE | Automatic reconnection/event IDs; ordinary HTTP response |
| Streaming download/model output | Streaming HTTP/SSE | No client-to-server message channel required |
| Frequent two-way messages | WebSocket | Persistent full-duplex channel |
| Peer-to-peer media/data | WebRTC | Media, NAT traversal, and peer connectivity capabilities |

Do not send large file uploads over WebSocket unless the application truly needs one multiplexed channel and owns chunking, resume, integrity, quotas, and storage flow control. Direct HTTP/object-storage upload is usually better.

---

## References

- [The WebSocket Protocol — RFC 6455](https://www.rfc-editor.org/rfc/rfc6455)
- [Bootstrapping WebSockets with HTTP/2 — RFC 8441](https://www.rfc-editor.org/rfc/rfc8441)
- [Bootstrapping WebSockets with HTTP/3 — RFC 9220](https://www.rfc-editor.org/rfc/rfc9220)
- [WHATWG WebSockets Standard](https://websockets.spec.whatwg.org/)

---

**Next**: [Message Protocols and Contracts](02_message_protocols_and_contracts.md)

