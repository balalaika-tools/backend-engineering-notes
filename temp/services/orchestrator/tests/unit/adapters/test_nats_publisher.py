"""NATS publish failures stay inside the outbox retry boundary."""

import asyncio
import uuid
from typing import Any, cast

import pytest
from nats.js.client import JetStreamContext
from nats.js.errors import NoStreamResponseError
from orchestrator.adapters.nats_publisher import NatsEventPublisher, NatsJetStreamPublisher
from orchestrator.ports.event_publisher import EventPublishError


class MissingStream:
    async def publish(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise NoStreamResponseError


class ConnectedClient:
    is_closed = False

    def jetstream(self) -> object:
        return object()


@pytest.mark.asyncio
async def test_missing_stream_is_translated_for_outbox_retry() -> None:
    publisher = NatsJetStreamPublisher(
        cast(JetStreamContext, cast(Any, MissingStream())),
        timeout_seconds=0.1,
    )

    with pytest.raises(EventPublishError, match="no response from stream"):
        await publisher.publish(
            subject="tests.missing",
            payload={"traceparent": "00-test"},
            message_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_initial_connection_attempt_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = asyncio.Event()

    async def never_connect(*_args: object, **_kwargs: object) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr("orchestrator.adapters.nats_publisher.nats.connect", never_connect)
    publisher = NatsEventPublisher("nats://unavailable", timeout_seconds=0.01)

    with pytest.raises(EventPublishError):
        await publisher.publish(
            subject="tests.missing",
            payload={},
            message_id=uuid.uuid4(),
        )

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_connection_forwards_the_authentication_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_options: dict[str, object] = {}

    async def connect(url: str, **kwargs: object) -> ConnectedClient:
        connection_options["url"] = url
        connection_options.update(kwargs)
        return ConnectedClient()

    monkeypatch.setattr("orchestrator.adapters.nats_publisher.nats.connect", connect)
    publisher = NatsEventPublisher(
        "nats://private.internal:4222",
        token="nats-secret",
    )

    await publisher._publisher()  # noqa: SLF001

    assert connection_options["url"] == "nats://private.internal:4222"
    assert connection_options["token"] == "nats-secret"
