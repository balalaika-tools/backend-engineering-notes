# FastAPI WebSockets

A complete guide to WebSockets in FastAPI — from mental model to production-ready patterns.

---

## 1. The Mental Model

### HTTP vs WebSocket

HTTP is a **request-response** protocol. The client asks, the server answers, the connection is done.

WebSocket is a **persistent bidirectional** channel. Both sides can send messages at any time, for as long as the connection stays open.

```
HTTP:
  Client ──request──>  Server
  Client <──response── Server
  (connection closed)

WebSocket:
  Client ──upgrade──>  Server
  Client <──accept───  Server
  Client <──message──> Server    ← both directions, any time
  Client <──message──> Server
  ...                            ← stays open indefinitely
  Client ──close──>    Server
```

### When to Use WebSockets

| Use Case | Protocol | Why |
|----------|----------|-----|
| Fetch a resource | HTTP | Single request, single response |
| Submit a form | HTTP | One-shot operation |
| Live chat | WebSocket | Continuous bidirectional messages |
| Real-time notifications | WebSocket | Server pushes updates as they happen |
| Collaborative editing | WebSocket | Multiple users, real-time sync |
| Live dashboards | WebSocket | Continuous data stream from server |
| File upload | HTTP | One-shot, better tooling for multipart |
| Polling every 30s | HTTP | Interval is long enough that persistent connection wastes resources |

**Rule of thumb**: If the server needs to push data to the client **unprompted** and **frequently**, use WebSocket. Otherwise, HTTP (or SSE) is simpler.

### The Upgrade Handshake

WebSocket connections start as HTTP. The client sends an HTTP `Upgrade` request, and the server responds with `101 Switching Protocols`. After that, the connection speaks the WebSocket protocol — not HTTP.

```
GET /ws HTTP/1.1
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==

HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
```

This matters because:
- **No HTTP headers** after the handshake — you cannot set `Authorization` headers per message
- **No HTTP status codes** — errors are WebSocket close frames, not 4xx/5xx
- **Load balancers must support WebSocket** — or the upgrade fails silently

---

## 2. FastAPI WebSocket Basics

### Minimal WebSocket Endpoint

```python
from fastapi import FastAPI, WebSocket

app = FastAPI()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Echo: {data}")
```

Three mandatory steps:
1. **Accept** the connection — `await websocket.accept()`
2. **Receive** messages — `await websocket.receive_text()` or `receive_json()`
3. **Send** messages — `await websocket.send_text()` or `send_json()`

### Send and Receive Methods

| Method | Data Type | Use For |
|--------|-----------|---------|
| `receive_text()` | `str` | Plain text messages |
| `receive_bytes()` | `bytes` | Binary data (images, files) |
| `receive_json()` | `dict`/`list` | Structured data (most common) |
| `send_text(data)` | `str` | Plain text responses |
| `send_bytes(data)` | `bytes` | Binary responses |
| `send_json(data)` | `dict`/`list` | Structured responses (most common) |

### Path and Query Parameters

WebSocket endpoints support the same parameter extraction as HTTP endpoints:

```python
@app.websocket("/ws/{room_id}")
async def room_socket(websocket: WebSocket, room_id: str, token: str = None):
    # room_id from path: /ws/general
    # token from query:  /ws/general?token=abc123
    await websocket.accept()
    ...
```

---

## 3. Connection Lifecycle

### The Full Lifecycle

Every WebSocket connection goes through these phases:

```
CONNECTING  →  OPEN  →  CLOSING  →  CLOSED
    │             │          │
  accept()   message loop  close() or disconnect
```

### Production-Ready Lifecycle Pattern

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import logging

app = FastAPI()
logger = logging.getLogger(__name__)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id = id(websocket)
    logger.info("ws_connected", extra={"client_id": client_id})

    try:
        while True:
            message = await websocket.receive_json()
            response = await handle_message(message)
            await websocket.send_json(response)

    except WebSocketDisconnect:
        logger.info("ws_disconnected", extra={"client_id": client_id})

    finally:
        # Cleanup: remove from connection managers, release resources
        logger.info("ws_cleanup", extra={"client_id": client_id})
```

### Why `try/finally` Is Non-Negotiable

Without `finally`, disconnections leak resources:

```python
# ❌ BAD: resource leak on disconnect
@app.websocket("/ws")
async def bad_endpoint(websocket: WebSocket):
    await websocket.accept()
    manager.add(websocket)
    while True:
        data = await websocket.receive_text()  # raises on disconnect
        await websocket.send_text(data)
    # manager.remove() never called if client disconnects
```

```python
# ✅ GOOD: cleanup always runs
@app.websocket("/ws")
async def good_endpoint(websocket: WebSocket):
    await websocket.accept()
    manager.add(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(data)
    except WebSocketDisconnect:
        pass
    finally:
        manager.remove(websocket)
```

---

## 4. JSON Messaging Protocol

### Why JSON Messages Need a Protocol

Raw WebSocket gives you a pipe. You need structure on top — message types, request IDs, error formats. Without this, your client and server have no shared language.

### Defining a Message Protocol

```python
from pydantic import BaseModel
from typing import Literal
from datetime import datetime

class IncomingMessage(BaseModel):
    type: str
    payload: dict = {}
    request_id: str | None = None

class OutgoingMessage(BaseModel):
    type: str
    payload: dict = {}
    request_id: str | None = None
    timestamp: str

    @classmethod
    def create(cls, type: str, payload: dict = {}, request_id: str | None = None):
        return cls(
            type=type,
            payload=payload,
            request_id=request_id,
            timestamp=datetime.utcnow().isoformat(),
        )
```

### Message Router Pattern

```python
from pydantic import ValidationError

async def handle_message(websocket: WebSocket, raw: dict) -> None:
    try:
        message = IncomingMessage(**raw)
    except ValidationError as e:
        await websocket.send_json(
            OutgoingMessage.create(
                type="error",
                payload={"detail": "Invalid message format", "errors": e.errors()},
            ).model_dump()
        )
        return

    handler = MESSAGE_HANDLERS.get(message.type)
    if not handler:
        await websocket.send_json(
            OutgoingMessage.create(
                type="error",
                payload={"detail": f"Unknown message type: {message.type}"},
                request_id=message.request_id,
            ).model_dump()
        )
        return

    result = await handler(message.payload)
    await websocket.send_json(
        OutgoingMessage.create(
            type=f"{message.type}_response",
            payload=result,
            request_id=message.request_id,
        ).model_dump()
    )


# Register handlers
async def handle_chat(payload: dict) -> dict:
    text = payload.get("text", "")
    return {"reply": f"You said: {text}"}

async def handle_subscribe(payload: dict) -> dict:
    channel = payload.get("channel", "default")
    return {"subscribed": channel}

MESSAGE_HANDLERS = {
    "chat": handle_chat,
    "subscribe": handle_subscribe,
}
```

### Example Message Flow

```json
// Client sends:
{"type": "chat", "payload": {"text": "hello"}, "request_id": "abc-123"}

// Server responds:
{"type": "chat_response", "payload": {"reply": "You said: hello"}, "request_id": "abc-123", "timestamp": "2024-01-15T10:30:00"}

// Client sends unknown type:
{"type": "unknown_thing", "payload": {}}

// Server responds:
{"type": "error", "payload": {"detail": "Unknown message type: unknown_thing"}, "request_id": null, "timestamp": "2024-01-15T10:30:01"}
```

---

## 5. Connection Manager Pattern

### Why You Need One

In a real application, you need to:
- Track who is connected
- Broadcast messages to all clients
- Send messages to specific rooms/channels
- Clean up when clients disconnect

A connection manager centralizes this logic.

### Basic Connection Manager

```python
from fastapi import WebSocket
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, client_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[client_id] = websocket
        logger.info("client_connected", extra={
            "client_id": client_id,
            "total_connections": len(self._connections),
        })

    def disconnect(self, client_id: str) -> None:
        self._connections.pop(client_id, None)
        logger.info("client_disconnected", extra={
            "client_id": client_id,
            "total_connections": len(self._connections),
        })

    async def send_to(self, client_id: str, message: dict) -> None:
        websocket = self._connections.get(client_id)
        if websocket:
            await websocket.send_json(message)

    async def broadcast(self, message: dict, exclude: str | None = None) -> None:
        dead_connections = []
        for client_id, websocket in self._connections.items():
            if client_id == exclude:
                continue
            try:
                await websocket.send_json(message)
            except Exception:
                dead_connections.append(client_id)

        for client_id in dead_connections:
            self.disconnect(client_id)


manager = ConnectionManager()
```

### Room-Based Connection Manager

```python
from collections import defaultdict


class RoomManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}
        self._rooms: dict[str, set[str]] = defaultdict(set)

    async def connect(self, client_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[client_id] = websocket

    def disconnect(self, client_id: str) -> None:
        self._connections.pop(client_id, None)
        # Remove from all rooms
        for room_members in self._rooms.values():
            room_members.discard(client_id)
        # Clean up empty rooms
        empty_rooms = [r for r, members in self._rooms.items() if not members]
        for room in empty_rooms:
            del self._rooms[room]

    def join_room(self, client_id: str, room: str) -> None:
        self._rooms[room].add(client_id)

    def leave_room(self, client_id: str, room: str) -> None:
        self._rooms[room].discard(client_id)

    async def broadcast_to_room(
        self, room: str, message: dict, exclude: str | None = None
    ) -> None:
        members = self._rooms.get(room, set())
        dead = []
        for client_id in members:
            if client_id == exclude:
                continue
            websocket = self._connections.get(client_id)
            if websocket:
                try:
                    await websocket.send_json(message)
                except Exception:
                    dead.append(client_id)

        for client_id in dead:
            self.disconnect(client_id)

    def get_room_members(self, room: str) -> set[str]:
        return self._rooms.get(room, set()).copy()

    def get_rooms_for_client(self, client_id: str) -> list[str]:
        return [r for r, members in self._rooms.items() if client_id in members]


room_manager = RoomManager()
```

### Using the Room Manager

```python
@app.websocket("/ws/chat/{room_id}")
async def chat_endpoint(websocket: WebSocket, room_id: str):
    client_id = str(id(websocket))

    await room_manager.connect(client_id, websocket)
    room_manager.join_room(client_id, room_id)

    try:
        await room_manager.broadcast_to_room(
            room_id,
            {"type": "system", "payload": {"text": f"{client_id} joined"}},
            exclude=client_id,
        )

        while True:
            data = await websocket.receive_json()
            await room_manager.broadcast_to_room(
                room_id,
                {"type": "chat", "payload": {"sender": client_id, "text": data["text"]}},
            )

    except WebSocketDisconnect:
        room_manager.disconnect(client_id)
        await room_manager.broadcast_to_room(
            room_id,
            {"type": "system", "payload": {"text": f"{client_id} left"}},
        )

    finally:
        room_manager.disconnect(client_id)
```

---

## 6. Authentication

### The WebSocket Auth Problem

HTTP endpoints authenticate via headers:

```
GET /api/data HTTP/1.1
Authorization: Bearer eyJhb...
```

WebSocket connections **cannot set custom headers** from browser JavaScript. The `WebSocket()` constructor does not accept an `Authorization` header. This is a browser limitation, not a protocol one.

### Authentication Strategies

| Strategy | How | Pros | Cons |
|----------|-----|------|------|
| Query parameter | `ws://host/ws?token=xxx` | Simple, works everywhere | Token visible in logs/URLs |
| First message | Connect, then send `{"type": "auth", "token": "xxx"}` | Token not in URL | Window between connect and auth |
| Cookie | Browser sends cookie automatically | Transparent | Requires cookie-based auth |
| Ticket/one-time token | HTTP endpoint issues short-lived ticket, WS uses it | Secure, token expires | Extra HTTP round trip |

### Query Parameter Authentication (Simple)

```python
from fastapi import WebSocket, WebSocketException, status
import jwt

SECRET_KEY = "your-secret-key"

async def authenticate_websocket(websocket: WebSocket) -> dict:
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.InvalidTokenError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)


@app.websocket("/ws")
async def authenticated_endpoint(websocket: WebSocket):
    user = await authenticate_websocket(websocket)
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            await websocket.send_json({
                "user": user["sub"],
                "echo": data,
            })
    except WebSocketDisconnect:
        pass
```

**Client-side:**

```javascript
const ws = new WebSocket("ws://localhost:8000/ws?token=eyJhb...");
```

### Ticket-Based Authentication (Production)

More secure — the token never appears in WebSocket URLs or logs.

```python
import secrets
from datetime import datetime, timedelta
from fastapi import HTTPException

# In-memory ticket store (use Redis in production)
_tickets: dict[str, dict] = {}

@app.post("/ws/ticket")
async def create_ws_ticket(user: dict = Depends(get_current_user)):
    """HTTP endpoint: exchange auth token for a short-lived WS ticket."""
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = {
        "user": user,
        "expires": datetime.utcnow() + timedelta(seconds=30),
    }
    return {"ticket": ticket}


async def validate_ticket(websocket: WebSocket) -> dict:
    ticket = websocket.query_params.get("ticket")
    if not ticket or ticket not in _tickets:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    entry = _tickets.pop(ticket)  # one-time use

    if datetime.utcnow() > entry["expires"]:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    return entry["user"]


@app.websocket("/ws")
async def secure_endpoint(websocket: WebSocket):
    user = await validate_ticket(websocket)
    await websocket.accept()
    ...
```

**Client-side flow:**

```javascript
// Step 1: Get ticket via authenticated HTTP call
const resp = await fetch("/ws/ticket", {
    method: "POST",
    headers: { "Authorization": "Bearer eyJhb..." },
});
const { ticket } = await resp.json();

// Step 2: Connect WebSocket with ticket (short-lived, one-time)
const ws = new WebSocket(`ws://localhost:8000/ws?ticket=${ticket}`);
```

---

## 7. Heartbeat / Ping-Pong

### The Dead Connection Problem

TCP connections can die silently:
- Client loses network (laptop lid closed, WiFi drop)
- NAT/firewall timeout (common after 30-60s of idle)
- Intermediate proxy drops the connection

Without heartbeats, the server holds dead connections indefinitely — wasting memory and corrupting connection counts.

### Application-Level Heartbeat

The WebSocket protocol has built-in ping/pong frames, but not all proxies forward them reliably. Application-level heartbeats are more predictable.

```python
import asyncio
from fastapi import WebSocket, WebSocketDisconnect

HEARTBEAT_INTERVAL = 30  # seconds
HEARTBEAT_TIMEOUT = 10   # seconds to wait for pong


async def heartbeat_loop(websocket: WebSocket, stop_event: asyncio.Event):
    """Send periodic pings, detect dead connections."""
    try:
        while not stop_event.is_set():
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    websocket.send_json({"type": "ping"}),
                    timeout=HEARTBEAT_TIMEOUT,
                )
            except (asyncio.TimeoutError, Exception):
                logger.warning("heartbeat_failed, closing connection")
                await websocket.close()
                break
    except asyncio.CancelledError:
        pass


@app.websocket("/ws")
async def endpoint_with_heartbeat(websocket: WebSocket):
    await websocket.accept()
    stop_event = asyncio.Event()
    heartbeat_task = asyncio.create_task(heartbeat_loop(websocket, stop_event))

    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "pong":
                continue  # heartbeat response, ignore
            await handle_message(websocket, message)

    except WebSocketDisconnect:
        pass

    finally:
        stop_event.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
```

### Client-Side Heartbeat Handling

```javascript
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "ping") {
        ws.send(JSON.stringify({ type: "pong" }));
        return;
    }
    // handle other messages...
};
```

### Heartbeat Configuration Guidelines

| Environment | Interval | Timeout | Rationale |
|-------------|----------|---------|-----------|
| Internal network | 30s | 10s | Low latency, detect fast |
| Public internet | 25s | 15s | Stay under common 30s NAT timeouts |
| Mobile clients | 20s | 15s | Unreliable networks |
| Long-running tasks | 15s | 10s | Need fast failure detection |

---

## 8. Error Handling

### WebSocket Close Codes

WebSocket has its own set of status codes (not HTTP codes):

| Code | Name | Meaning |
|------|------|---------|
| 1000 | Normal Closure | Clean shutdown |
| 1001 | Going Away | Server shutting down, client navigating away |
| 1003 | Unsupported Data | Received data type server cannot handle |
| 1008 | Policy Violation | Auth failure, forbidden action |
| 1011 | Internal Error | Server encountered an unexpected condition |
| 1012 | Service Restart | Server is restarting, client should reconnect |

### Comprehensive Error Handling Pattern

```python
from starlette.websockets import WebSocketState

@app.websocket("/ws")
async def robust_endpoint(websocket: WebSocket):
    try:
        await websocket.accept()
    except Exception:
        logger.error("ws_accept_failed")
        return

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except ValueError:
                # Client sent non-JSON data
                await websocket.send_json({
                    "type": "error",
                    "payload": {"detail": "Invalid JSON"},
                })
                continue

            try:
                result = await process_message(raw)
                await websocket.send_json(result)
            except BusinessLogicError as e:
                await websocket.send_json({
                    "type": "error",
                    "payload": {"detail": str(e)},
                })
            except Exception:
                logger.exception("unhandled_error_in_message_handler")
                await websocket.send_json({
                    "type": "error",
                    "payload": {"detail": "Internal server error"},
                })

    except WebSocketDisconnect:
        logger.info("client_disconnected")

    except Exception:
        logger.exception("ws_unexpected_error")
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011)

    finally:
        # Always clean up, regardless of how we got here
        await cleanup_resources(websocket)
```

### Key Error Handling Rules

**Do not let a single bad message kill the connection:**

```python
# ❌ BAD: one bad message kills the whole connection
while True:
    data = await websocket.receive_json()
    result = might_throw(data)  # unhandled exception = connection dead
    await websocket.send_json(result)
```

```python
# ✅ GOOD: errors are handled per-message, connection survives
while True:
    data = await websocket.receive_json()
    try:
        result = might_throw(data)
        await websocket.send_json(result)
    except Exception as e:
        await websocket.send_json({"type": "error", "payload": {"detail": str(e)}})
```

**Always check connection state before sending:**

```python
# ❌ BAD: sending to a possibly-closed connection
async def broadcast(self, message: dict):
    for ws in self._connections.values():
        await ws.send_json(message)  # crashes if ws is closed
```

```python
# ✅ GOOD: handle send failures gracefully
async def broadcast(self, message: dict):
    dead = []
    for client_id, ws in self._connections.items():
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(client_id)
    for client_id in dead:
        self.disconnect(client_id)
```

---

## 9. Scaling Considerations

### The Statefulness Problem

HTTP is stateless — any server can handle any request. WebSocket connections are **stateful** — the connection lives on a specific server process.

```
HTTP (stateless):
  Client A ──req──> [Load Balancer] ──> Server 1   ← any server works
  Client A ──req──> [Load Balancer] ──> Server 2   ← different server, fine

WebSocket (stateful):
  Client A ══ws══> [Load Balancer] ══> Server 1    ← must stay on Server 1
  Client B ══ws══> [Load Balancer] ══> Server 2    ← must stay on Server 2
```

This creates three problems:
1. **Sticky sessions**: The load balancer must route the WebSocket connection to the same backend for its entire lifetime
2. **Cross-server messaging**: If Client A (on Server 1) wants to message Client B (on Server 2), the message must traverse servers
3. **Uneven load**: Long-lived connections cause uneven distribution

### Single-Server Architecture (Sufficient for Many Apps)

If you have one server process, the `ConnectionManager` from Section 5 works directly. All connections are in-process.

**This is fine when:**
- You have fewer than ~10,000 concurrent connections
- You run a single process (or one Uvicorn worker)
- You do not need horizontal scaling

### Multi-Server with Redis Pub/Sub

For multiple server processes or pods, use Redis as a message bus between them.

```
┌────────────┐     ┌────────────┐     ┌────────────┐
│  Server 1  │     │   Redis    │     │  Server 2  │
│            │────>│  Pub/Sub   │<────│            │
│ Client A   │<────│            │────>│ Client B   │
│ Client C   │     │            │     │ Client D   │
└────────────┘     └────────────┘     └────────────┘
```

```python
import redis.asyncio as redis
import json
import asyncio

REDIS_URL = "redis://localhost:6379"


class RedisConnectionManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}
        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None

    async def startup(self):
        self._redis = redis.from_url(REDIS_URL)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe("ws:broadcast")
        asyncio.create_task(self._listen_redis())

    async def shutdown(self):
        if self._pubsub:
            await self._pubsub.unsubscribe("ws:broadcast")
            await self._pubsub.close()
        if self._redis:
            await self._redis.close()

    async def _listen_redis(self):
        """Listen for messages from other server instances."""
        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            data = json.loads(message["data"])
            # Deliver to local connections only
            await self._deliver_local(data["payload"], exclude=data.get("exclude"))

    async def _deliver_local(self, payload: dict, exclude: str | None = None):
        dead = []
        for client_id, ws in self._connections.items():
            if client_id == exclude:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(client_id)
        for client_id in dead:
            self._connections.pop(client_id, None)

    async def connect(self, client_id: str, websocket: WebSocket):
        await websocket.accept()
        self._connections[client_id] = websocket

    def disconnect(self, client_id: str):
        self._connections.pop(client_id, None)

    async def broadcast(self, message: dict, exclude: str | None = None):
        """Publish to Redis so ALL server instances deliver the message."""
        await self._redis.publish(
            "ws:broadcast",
            json.dumps({"payload": message, "exclude": exclude}),
        )


redis_manager = RedisConnectionManager()
```

### Wiring Into FastAPI Lifespan

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_manager.startup()
    yield
    await redis_manager.shutdown()

app = FastAPI(lifespan=lifespan)
```

### Load Balancer Configuration

Your load balancer must:
1. **Support WebSocket upgrade** (most do: Nginx, HAProxy, AWS ALB)
2. **Use sticky sessions** (or IP hash) for WebSocket routes
3. **Set appropriate timeouts** (WebSocket idle timeout > your heartbeat interval)

Nginx example:

```nginx
upstream backend {
    ip_hash;  # sticky sessions
    server app1:8000;
    server app2:8000;
}

location /ws {
    proxy_pass http://backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400s;  # 24h — let heartbeats handle dead connections
    proxy_send_timeout 86400s;
}
```

---

## 10. Common Mistakes

### Forgetting to Accept

```python
# ❌ BAD: sends before accepting — runtime error
@app.websocket("/ws")
async def endpoint(websocket: WebSocket):
    await websocket.send_text("hello")  # Error: connection not accepted
```

```python
# ✅ GOOD: always accept first
@app.websocket("/ws")
async def endpoint(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_text("hello")
```

### Not Handling Disconnect

```python
# ❌ BAD: unhandled WebSocketDisconnect crashes the handler
@app.websocket("/ws")
async def endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()  # raises when client leaves
        await websocket.send_text(data)
```

```python
# ✅ GOOD: catch disconnect, clean up
@app.websocket("/ws")
async def endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(data)
    except WebSocketDisconnect:
        pass
```

### Blocking the Event Loop

```python
# ❌ BAD: CPU-bound work blocks ALL other WebSocket connections
@app.websocket("/ws")
async def endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_json()
        result = heavy_computation(data)  # blocks the event loop
        await websocket.send_json(result)
```

```python
# ✅ GOOD: offload CPU-bound work to a thread pool
import asyncio

@app.websocket("/ws")
async def endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_json()
        result = await asyncio.to_thread(heavy_computation, data)
        await websocket.send_json(result)
```

### Using `id(websocket)` as a Stable Client ID

```python
# ❌ BAD: id() is only unique for the object's lifetime, not globally unique
client_id = str(id(websocket))
```

```python
# ✅ GOOD: use a proper unique identifier
import uuid
client_id = str(uuid.uuid4())
```

### No Backpressure on Broadcast

```python
# ❌ BAD: slow client blocks broadcast to all other clients
async def broadcast(self, message: dict):
    for ws in self._connections.values():
        await ws.send_json(message)  # if one client is slow, everyone waits
```

```python
# ✅ GOOD: per-client send queues with timeout
async def broadcast(self, message: dict):
    async def _send(client_id: str, ws: WebSocket):
        try:
            await asyncio.wait_for(ws.send_json(message), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            self.disconnect(client_id)

    await asyncio.gather(
        *[_send(cid, ws) for cid, ws in self._connections.items()]
    )
```

### Not Cleaning Up Background Tasks

```python
# ❌ BAD: heartbeat task leaks after disconnect
@app.websocket("/ws")
async def endpoint(websocket: WebSocket):
    await websocket.accept()
    asyncio.create_task(heartbeat_loop(websocket))  # who cancels this?
    while True:
        data = await websocket.receive_json()
```

```python
# ✅ GOOD: cancel background tasks on disconnect
@app.websocket("/ws")
async def endpoint(websocket: WebSocket):
    await websocket.accept()
    heartbeat = asyncio.create_task(heartbeat_loop(websocket))
    try:
        while True:
            data = await websocket.receive_json()
    except WebSocketDisconnect:
        pass
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
```

---

## Quick Reference

### Minimal Production Endpoint

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncio
import uuid
import logging

app = FastAPI()
logger = logging.getLogger(__name__)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_id = str(uuid.uuid4())
    await manager.connect(client_id, websocket)

    try:
        while True:
            raw = await websocket.receive_json()
            try:
                result = await handle_message(raw)
                await websocket.send_json(result)
            except Exception as e:
                logger.exception("message_handler_error")
                await websocket.send_json({
                    "type": "error",
                    "payload": {"detail": "Internal error"},
                })

    except WebSocketDisconnect:
        logger.info("disconnected", extra={"client_id": client_id})

    finally:
        manager.disconnect(client_id)
```

### Checklist

Before shipping WebSocket endpoints to production:

- [ ] `await websocket.accept()` is called before any send/receive
- [ ] `WebSocketDisconnect` is caught
- [ ] `try/finally` ensures cleanup (connection manager, background tasks)
- [ ] CPU-bound work is offloaded with `asyncio.to_thread()`
- [ ] Heartbeats are implemented for idle connection detection
- [ ] Authentication happens **before** `accept()` or **immediately after**
- [ ] Broadcast handles slow/dead clients without blocking others
- [ ] Background tasks are cancelled on disconnect
- [ ] Load balancer is configured for WebSocket upgrade and sticky sessions
- [ ] If multi-pod: Redis pub/sub (or similar) bridges cross-server messages
