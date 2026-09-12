"""Durable pull consumer with explicit JetStream acknowledgement controls."""

import asyncio
import logging
from dataclasses import dataclass

from nats.aio.msg import Msg
from nats.errors import (
    ConnectionClosedError,
    ConnectionDrainingError,
    ConnectionReconnectingError,
)
from nats.errors import (
    TimeoutError as NatsTimeoutError,
)
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.client import JetStreamContext
from nats.js.errors import FetchTimeoutError, NotFoundError
from pydantic import ValidationError
from worker.adapters.nats.serialization import InvestigationRequestedPayload
from worker.domain.investigation import InvestigationRequested

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NatsConsumerSettings:
    stream: str
    consumer: str
    subject: str
    ack_wait_seconds: float
    max_deliver: int
    max_ack_pending: int
    duplicate_window_seconds: float
    replicas: int = 1


class NatsInvestigationMessage:
    def __init__(self, message: Msg) -> None:
        self._message = message
        self._event = InvestigationRequestedPayload.model_validate_json(message.data).to_domain()

    @property
    def event(self) -> InvestigationRequested:
        return self._event

    @property
    def trace_carrier(self) -> dict[str, str]:
        headers = self._message.headers or {}
        value = headers.get("traceparent") or self._event.traceparent
        return {"traceparent": value} if value else {}

    async def ack(self) -> None:
        await self._message.ack_sync()

    async def nak(self, *, delay_seconds: float | None = None) -> None:
        await self._message.nak(delay=delay_seconds)

    async def term(self) -> None:
        await self._message.term()

    async def in_progress(self) -> None:
        await self._message.in_progress()


class NatsInvestigationConsumer:
    def __init__(self, jetstream: JetStreamContext, *, settings: NatsConsumerSettings) -> None:
        self._jetstream = jetstream
        self._settings = settings
        self._subscription: JetStreamContext.PullSubscription | None = None

    async def ensure(self) -> None:
        stream_config = StreamConfig(
            name=self._settings.stream,
            subjects=[self._settings.subject],
            retention=RetentionPolicy.WORK_QUEUE,
            storage=StorageType.FILE,
            num_replicas=self._settings.replicas,
            duplicate_window=self._settings.duplicate_window_seconds,
        )
        try:
            stream_info = await self._jetstream.stream_info(self._settings.stream)
        except NotFoundError:
            await self._jetstream.add_stream(config=stream_config)
        else:
            stream_config = stream_info.config.evolve(
                subjects=[self._settings.subject],
                retention=RetentionPolicy.WORK_QUEUE,
                storage=StorageType.FILE,
                duplicate_window=self._settings.duplicate_window_seconds,
            )
            await self._jetstream.update_stream(config=stream_config)

        consumer_config = ConsumerConfig(
            durable_name=self._settings.consumer,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=self._settings.ack_wait_seconds,
            max_deliver=self._settings.max_deliver,
            max_ack_pending=self._settings.max_ack_pending,
            filter_subject=self._settings.subject,
        )
        await self._jetstream.add_consumer(self._settings.stream, config=consumer_config)
        self._subscription = await self._jetstream.pull_subscribe(
            self._settings.subject,
            durable=self._settings.consumer,
            stream=self._settings.stream,
            pending_msgs_limit=1,
        )

    async def fetch_one(self, *, timeout_seconds: float) -> NatsInvestigationMessage | None:
        if self._subscription is None:
            raise RuntimeError("Consumer must be ensured before fetching")
        try:
            messages = await self._subscription.fetch(batch=1, timeout=timeout_seconds)
        except (FetchTimeoutError, NatsTimeoutError):
            return None
        except (
            ConnectionClosedError,
            ConnectionDrainingError,
            ConnectionReconnectingError,
        ) as exc:
            logger.warning("nats_fetch_unavailable", exc_info=exc)
            await asyncio.sleep(max(0.1, min(timeout_seconds, 1.0)))
            return None
        message = messages[0]
        try:
            return NatsInvestigationMessage(message)
        except (ValidationError, ValueError) as exc:
            logger.error("invalid_investigation_message", exc_info=exc)
            try:
                await message.term()
            except (
                ConnectionClosedError,
                ConnectionDrainingError,
                ConnectionReconnectingError,
            ) as term_error:
                logger.warning("invalid_message_term_failed", exc_info=term_error)
            return None
