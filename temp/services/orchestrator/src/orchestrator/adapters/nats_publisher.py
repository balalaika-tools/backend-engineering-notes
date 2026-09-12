"""NATS JetStream event publisher."""

import asyncio
import json
import uuid
from collections.abc import Mapping

import nats
from nats.aio.client import Client as NatsClient
from nats.errors import (
    ConnectionClosedError,
    ConnectionReconnectingError,
    NoRespondersError,
    NoServersError,
)
from nats.errors import (
    TimeoutError as NatsTimeoutError,
)
from nats.js.client import JetStreamContext
from nats.js.errors import Error as JetStreamError
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract, inject
from orchestrator.ports.event_publisher import EventPublishError


class NatsJetStreamPublisher:
    def __init__(
        self,
        jetstream: JetStreamContext,
        *,
        timeout_seconds: float = 5.0,
        tracer: trace.Tracer | None = None,
    ) -> None:
        self._jetstream = jetstream
        self._timeout_seconds = timeout_seconds
        self._tracer = tracer or trace.get_tracer(__name__)

    async def publish(
        self,
        *,
        subject: str,
        payload: Mapping[str, object],
        message_id: uuid.UUID,
    ) -> None:
        carrier: dict[str, str] = {}
        traceparent = payload.get("traceparent")
        if isinstance(traceparent, str) and traceparent:
            carrier["traceparent"] = traceparent
        parent = extract(carrier, context=otel_context.Context())
        with self._tracer.start_as_current_span(
            "send investigations.requested",
            context=parent,
            kind=trace.SpanKind.PRODUCER,
            record_exception=False,
            attributes={
                "messaging.system": "nats",
                "messaging.destination.name": subject,
                "messaging.operation.name": "send",
                "messaging.operation.type": "send",
            },
        ) as span:
            headers = {"Nats-Msg-Id": str(message_id)}
            inject(headers)
            try:
                await self._jetstream.publish(
                    subject,
                    json.dumps(payload, separators=(",", ":")).encode(),
                    headers=headers,
                    timeout=self._timeout_seconds,
                )
            except (
                ConnectionClosedError,
                ConnectionReconnectingError,
                NoRespondersError,
                TimeoutError,
                JetStreamError,
            ) as exc:
                span.set_attribute("error.type", type(exc).__name__)
                raise EventPublishError(str(exc)) from exc


class NatsEventPublisher:
    """Maintain a lazy NATS connection so broker outages do not block API startup."""

    def __init__(self, url: str, *, token: str | None = None, timeout_seconds: float = 5.0) -> None:
        self._url = url
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._client: NatsClient | None = None

    async def publish(
        self,
        *,
        subject: str,
        payload: Mapping[str, object],
        message_id: uuid.UUID,
    ) -> None:
        publisher = await self._publisher()
        await publisher.publish(
            subject=subject,
            payload=payload,
            message_id=message_id,
        )

    async def close(self) -> None:
        if self._client is None or self._client.is_closed:
            return
        try:
            await self._client.drain()
        except (ConnectionClosedError, ConnectionReconnectingError):
            await self._client.close()

    async def _publisher(self) -> NatsJetStreamPublisher:
        if self._client is not None and not self._client.is_closed:
            return NatsJetStreamPublisher(
                self._client.jetstream(),
                timeout_seconds=self._timeout_seconds,
            )
        try:
            self._client = await asyncio.wait_for(
                nats.connect(
                    self._url,
                    token=self._token,
                    connect_timeout=min(self._timeout_seconds, 2.0),
                    max_reconnect_attempts=-1,
                ),
                timeout=self._timeout_seconds,
            )
        except (NoServersError, NatsTimeoutError, OSError, TimeoutError) as exc:
            raise EventPublishError(str(exc)) from exc
        return NatsJetStreamPublisher(
            self._client.jetstream(),
            timeout_seconds=self._timeout_seconds,
        )
