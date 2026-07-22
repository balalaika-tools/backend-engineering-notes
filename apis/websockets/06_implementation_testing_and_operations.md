# WebSocket Implementation, Testing, and Operations

> **Who this is for**: Python/FastAPI practitioners implementing the protocol, security, and backpressure decisions from the earlier WebSocket chapters.

---

## 1. Reference FastAPI Implementation

This compact service demonstrates:

- Exact browser origin validation
- Signed, expiring session cookie authentication
- Required subprotocol negotiation
- One reader and one writer per socket
- Discriminated/validated client messages
- Bounded outbound queues
- Cleanup on every exit

The example channel carries only user-scoped notifications, avoiding a fake object authorization implementation. Add a scoped database policy before introducing object subscriptions.

```python
import asyncio
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Literal

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError


logger = logging.getLogger(__name__)
app = FastAPI()

SESSION_SECRET = os.environ["WS_SESSION_SECRET"].encode("utf-8")
ALLOWED_ORIGINS = {"https://app.example.com"}
SUBPROTOCOL = "notifications.v1"
MAX_INBOUND_BYTES = 16 * 1024
MAX_OUTBOUND_MESSAGES = 100


class ClientMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    type: Literal["app.ping", "notifications.subscribe"]
    message_id: str = Field(min_length=1, max_length=100)
    payload: dict[str, str] = Field(default_factory=dict)


def sign_session(user_id: str, expires_at: int) -> str:
    if "." in user_id:
        raise ValueError("user_id must not contain a dot")
    value = f"{user_id}.{expires_at}"
    signature = hmac.new(
        SESSION_SECRET,
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{value}.{signature}"


def verify_session(token: str | None) -> str | None:
    if token is None:
        return None
    try:
        user_id, expires_text, supplied_signature = token.split(".", maxsplit=2)
        expires_at = int(expires_text)
    except (ValueError, TypeError):
        return None

    signed_value = f"{user_id}.{expires_at}"
    expected_signature = hmac.new(
        SESSION_SECRET,
        signed_value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    if expires_at <= int(time.time()):
        return None
    return user_id


@dataclass(eq=False)
class CloseInstruction:
    code: int
    reason: str


@dataclass(eq=False)
class ClientConnection:
    websocket: WebSocket
    user_id: str
    outbound: asyncio.Queue[dict[str, object] | CloseInstruction] = field(
        default_factory=lambda: asyncio.Queue(maxsize=MAX_OUTBOUND_MESSAGES)
    )
    subscribed: bool = False


class ConnectionHub:
    def __init__(self) -> None:
        self._clients: set[ClientConnection] = set()
        self._lock = asyncio.Lock()

    async def add(self, client: ClientConnection) -> None:
        async with self._lock:
            self._clients.add(client)

    async def remove(self, client: ClientConnection) -> None:
        async with self._lock:
            self._clients.discard(client)

    async def publish_to_user(
        self,
        user_id: str,
        message: dict[str, object],
    ) -> None:
        async with self._lock:
            recipients = [
                client
                for client in self._clients
                if client.user_id == user_id and client.subscribed
            ]

        for client in recipients:
            try:
                client.outbound.put_nowait(message)
            except asyncio.QueueFull:
                # The event must also be recoverable from durable state.
                client.outbound.get_nowait()
                client.outbound.put_nowait(
                    CloseInstruction(code=1013, reason="slow consumer")
                )


hub = ConnectionHub()


async def send_messages(client: ClientConnection) -> None:
    while True:
        item = await client.outbound.get()
        if isinstance(item, CloseInstruction):
            await client.websocket.close(code=item.code, reason=item.reason)
            return
        await client.websocket.send_json(item)


async def receive_messages(client: ClientConnection) -> None:
    while True:
        raw_message = await client.websocket.receive_text()
        if len(raw_message.encode("utf-8")) > MAX_INBOUND_BYTES:
            await client.websocket.close(code=1009, reason="message too large")
            return

        try:
            message = ClientMessage.model_validate_json(raw_message)
        except ValidationError:
            await client.outbound.put(
                {
                    "version": 1,
                    "type": "error",
                    "payload": {
                        "code": "INVALID_MESSAGE",
                        "message": "Message does not match the protocol schema.",
                        "retryable": False,
                    },
                }
            )
            continue

        if message.type == "app.ping":
            await client.outbound.put(
                {
                    "version": 1,
                    "type": "app.pong",
                    "request_id": message.message_id,
                    "payload": {},
                }
            )
        elif message.type == "notifications.subscribe":
            client.subscribed = True
            await client.outbound.put(
                {
                    "version": 1,
                    "type": "notifications.subscribed",
                    "request_id": message.message_id,
                    "payload": {},
                }
            )


@app.websocket("/ws/notifications")
async def notifications_socket(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin not in ALLOWED_ORIGINS:
        await websocket.close(code=1008, reason="origin not allowed")
        return

    if SUBPROTOCOL not in websocket.scope.get("subprotocols", []):
        await websocket.close(code=1002, reason="subprotocol required")
        return

    user_id = verify_session(websocket.cookies.get("session"))
    if user_id is None:
        await websocket.close(code=1008, reason="authentication required")
        return

    await websocket.accept(subprotocol=SUBPROTOCOL)
    client = ClientConnection(websocket=websocket, user_id=user_id)
    await hub.add(client)

    sender = asyncio.create_task(send_messages(client), name=f"ws-send-{user_id}")
    receiver = asyncio.create_task(receive_messages(client), name=f"ws-receive-{user_id}")

    try:
        done, pending = await asyncio.wait(
            {sender, receiver},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        for task in done:
            if task.cancelled():
                continue
            error = task.exception()
            if error is not None and not isinstance(error, WebSocketDisconnect):
                logger.error(
                    "websocket_task_failed",
                    extra={"user_id": user_id, "error_type": type(error).__name__},
                )
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)
        await hub.remove(client)
```

The signed cookie is a focused example, not a replacement for the application's normal session/token service. Issue it from an HTTPS authentication flow with `Secure`, `HttpOnly`, and an appropriate `SameSite` policy. Support key rotation and revocation for real identities.

`publish_to_user` must be called only after a durable business change or from a broker consumer. If the notification is important, clients recover it from a read API/event log after a slow-consumer close.

---

## 2. Concurrency Rules

Keep one receive coroutine and one send coroutine per connection unless the WebSocket library explicitly supports more. Concurrent sends can interleave library state; concurrent receives commonly raise or produce undefined ownership.

```text
broker/business tasks ─┐
heartbeat task ────────┼──> bounded outbound queue ──> one sender ──> socket
command replies ───────┘

socket ──> one receiver ──> validate/authorize/dispatch
```

Do not hold the hub lock while awaiting network I/O. Snapshot recipients under the lock, then enqueue outside it. For very large fleets, replace global scans with indexed local subscription maps.

---

## 3. Framework Boundaries

FastAPI/Starlette HTTP middleware based on `BaseHTTPMiddleware` does not run for WebSocket scopes. Put WebSocket-specific authentication/logging in dependencies/endpoints or write pure ASGI middleware that handles both `http` and `websocket` scopes.

Configure limits at every hop:

- Server maximum frame/message size and queue behavior
- Reverse proxy Upgrade/Extended CONNECT support
- Proxy idle and maximum connection lifetime
- Load balancer connection and surge limits
- Worker graceful-shutdown deadline
- File descriptor/process limits

The shortest timeout/limit wins.

See [FastAPI WebSockets](../../fundamentals/fastapi/06_websockets.md) for connection managers, rooms, Redis fan-out, and framework details.

---

## 4. Test the Protocol

```python
import json
import time

from fastapi.testclient import TestClient

from app.main import app, sign_session


def test_subscribe_and_ping() -> None:
    client = TestClient(app)
    client.cookies.set("session", sign_session("usr_123", int(time.time()) + 60))

    with client.websocket_connect(
        "/ws/notifications",
        headers={"origin": "https://app.example.com"},
        subprotocols=["notifications.v1"],
    ) as socket:
        socket.send_text(
            json.dumps(
                {
                    "version": 1,
                    "type": "notifications.subscribe",
                    "message_id": "msg_1",
                    "payload": {},
                }
            )
        )
        assert socket.receive_json()["type"] == "notifications.subscribed"

        socket.send_text(
            json.dumps(
                {
                    "version": 1,
                    "type": "app.ping",
                    "message_id": "msg_2",
                    "payload": {},
                }
            )
        )
        pong = socket.receive_json()
        assert pong["type"] == "app.pong"
        assert pong["request_id"] == "msg_2"
```

Add tests for disallowed origin, missing subprotocol, expired cookie, malformed/oversized messages, cross-user publishing, queue overflow, abrupt disconnect, broker failure, restart, resume, and permission revocation.

---

## 5. Observability

Metrics:

- Handshake attempts, accept/reject by bounded reason
- Open connections and connection duration
- Messages/bytes by direction and bounded type
- Invalid/unauthorized messages
- Outbound queue depth and slow-consumer closes
- Close codes, abnormal disconnects, reconnect rate
- Heartbeat RTT/misses, broker lag, replay count
- Event-loop lag, memory, file descriptors

Logs include connection ID, principal/tenant, negotiated protocol, safe message/event IDs, close code, and duration. Traces can represent the handshake and individual important commands; tracing every ephemeral message may be too expensive.

---

## 6. Deployment and Incident Checklist

- Stop new handshakes before draining.
- Notify/close existing clients within a bounded grace period.
- Clients reconnect with jitter and resume/snapshot.
- Old protocol versions have a maximum supported lifetime.
- Local queues and broker consumers stop without silently losing durable events.
- Dashboard separates handshake, broker, slow-consumer, auth, and abnormal-close failures.
- Runbook can disable expensive subscriptions or shed new connections.
- Capacity tests include reconnect storms and worst-case fan-out, not only idle sockets.

---

**Next**: [Webhook Deep Dive](../webhooks/README.md)
