"""Explicit NATS acknowledgement controls."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from nats.aio.msg import Msg
from nats.js.api import StreamConfig
from nats.js.client import JetStreamContext
from worker.adapters.nats.consumer import (
    NatsConsumerSettings,
    NatsInvestigationConsumer,
    NatsInvestigationMessage,
)


class FakeMessage:
    def __init__(self) -> None:
        self.data = json.dumps(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "investigation.requested",
                "occurred_at": datetime.now(UTC).isoformat(),
                "investigation_id": str(uuid.uuid4()),
                "request_id": str(uuid.uuid4()),
                "client_id": "client",
                "exception_id": "EX-1",
            }
        ).encode()
        self.calls: list[tuple[str, float | None]] = []

    async def ack_sync(self) -> None:
        self.calls.append(("ack", None))

    async def nak(self, *, delay: float | None = None) -> None:
        self.calls.append(("nak", delay))

    async def term(self) -> None:
        self.calls.append(("term", None))

    async def in_progress(self) -> None:
        self.calls.append(("in_progress", None))


class FakeSubscription:
    def __init__(self, message: FakeMessage) -> None:
        self._message = message

    async def fetch(self, **_kwargs: object) -> list[FakeMessage]:
        return [self._message]


class FakeJetStream:
    def __init__(self, message: FakeMessage) -> None:
        self.subscription = FakeSubscription(message)
        self.updated_stream: Any = None
        self.updated_consumer: Any = None

    async def stream_info(self, _stream: str) -> object:
        return type(
            "StreamInfo",
            (),
            {"config": StreamConfig(name="investigations", num_replicas=3, max_msgs=99)},
        )()

    async def update_stream(self, *, config: object) -> None:
        self.updated_stream = config

    async def add_consumer(self, _stream: str, *, config: object) -> None:
        self.updated_consumer = config

    async def pull_subscribe(self, *_args: object, **_kwargs: object) -> FakeSubscription:
        return self.subscription


def _consumer(jetstream: FakeJetStream) -> NatsInvestigationConsumer:
    return NatsInvestigationConsumer(
        cast(JetStreamContext, cast(object, jetstream)),
        settings=NatsConsumerSettings(
            stream="investigations",
            consumer="workers",
            subject="investigations.requested",
            ack_wait_seconds=30,
            max_deliver=8,
            max_ack_pending=10,
            duplicate_window_seconds=120,
        ),
    )


@pytest.mark.asyncio
async def test_message_exposes_event_and_all_acknowledgement_actions() -> None:
    raw_message = FakeMessage()
    message = NatsInvestigationMessage(cast(Msg, raw_message))

    await message.in_progress()
    await message.nak(delay_seconds=5)
    await message.term()
    await message.ack()

    assert message.event.exception_id == "EX-1"
    assert raw_message.calls == [
        ("in_progress", None),
        ("nak", 5),
        ("term", None),
        ("ack", None),
    ]


@pytest.mark.asyncio
async def test_ensure_preserves_stream_replication_and_updates_consumer_settings() -> None:
    jetstream = FakeJetStream(FakeMessage())
    consumer = _consumer(jetstream)

    await consumer.ensure()

    assert jetstream.updated_stream.num_replicas == 3
    assert jetstream.updated_stream.max_msgs == 99
    assert jetstream.updated_consumer.ack_wait == 30
    assert jetstream.updated_consumer.max_ack_pending == 10


@pytest.mark.asyncio
async def test_invalid_payload_is_terminated_and_skipped() -> None:
    raw_message = FakeMessage()
    raw_message.data = b"not-json"
    jetstream = FakeJetStream(raw_message)
    consumer = _consumer(jetstream)
    await consumer.ensure()

    assert await consumer.fetch_one(timeout_seconds=0.1) is None
    assert raw_message.calls == [("term", None)]
