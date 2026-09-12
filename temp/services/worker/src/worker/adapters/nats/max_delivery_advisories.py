"""Translate JetStream max-delivery advisories into investigation notices."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription
from nats.js.client import JetStreamContext
from nats.js.errors import NotFoundError
from pydantic import ValidationError
from worker.adapters.nats.serialization import InvestigationRequestedPayload
from worker.application.reconcile import (
    MaxDeliveryOutcome,
    ReconciliationResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _MaxDeliveryNotice:
    investigation_id: uuid.UUID
    stream_sequence: int


class MaxDeliveryCoordinator:
    """Coordinate one advisory pass without leaking delivery identity inward."""

    def __init__(
        self,
        advisories: "NatsMaxDeliveryAdvisories",
        reconcile: Callable[[tuple[uuid.UUID, ...]], Awaitable[ReconciliationResult]],
    ) -> None:
        self._advisories = advisories
        self._reconcile = reconcile

    async def run_once(self) -> ReconciliationResult:
        notices = await self._advisories.receive()
        investigation_ids = tuple(dict.fromkeys(notice.investigation_id for notice in notices))
        result = await self._reconcile(investigation_ids)
        outcomes = {item.investigation_id: item.outcome for item in result.investigation_results}
        cleanup_outcomes = {
            MaxDeliveryOutcome.FAILED,
            MaxDeliveryOutcome.ALREADY_TERMINAL,
            MaxDeliveryOutcome.MISSING,
        }
        for notice in notices:
            if outcomes.get(notice.investigation_id) not in cleanup_outcomes:
                continue
            try:
                await self._advisories.delete_message(notice.stream_sequence)
            except Exception as exc:
                logger.error(
                    "max_delivery_message_delete_failed",
                    exc_info=exc,
                    extra={"stream_sequence": notice.stream_sequence},
                )
        return result


class NatsMaxDeliveryAdvisories:
    def __init__(
        self,
        client: Client,
        jetstream: JetStreamContext,
        *,
        stream: str,
        consumer: str,
    ) -> None:
        self._client = client
        self._jetstream = jetstream
        self._stream = stream
        self._subject = f"$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.{stream}.{consumer}"
        self._sequences: asyncio.Queue[int] = asyncio.Queue()
        self._pending: set[int] = set()
        self._subscription: Subscription | None = None

    async def start(self) -> None:
        if self._subscription is None:
            self._subscription = await self._client.subscribe(
                self._subject,
                cb=self._capture,
            )

    async def close(self) -> None:
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None

    async def receive(self) -> tuple[_MaxDeliveryNotice, ...]:
        notices: list[_MaxDeliveryNotice] = []
        while not self._sequences.empty():
            sequence = self._sequences.get_nowait()
            self._pending.add(sequence)
        for sequence in sorted(self._pending):
            try:
                raw = await self._jetstream.get_msg(self._stream, seq=sequence)
            except NotFoundError:
                self._pending.discard(sequence)
                continue
            try:
                if raw.data is None:
                    raise ValueError(f"JetStream message {sequence} has no payload")
                event = InvestigationRequestedPayload.model_validate_json(raw.data)
            except (ValidationError, ValueError) as exc:
                logger.error(
                    "invalid_max_delivery_message",
                    exc_info=exc,
                    extra={"stream_sequence": sequence},
                )
                try:
                    await self._jetstream.delete_msg(self._stream, sequence)
                except NotFoundError:
                    pass
                self._pending.discard(sequence)
                continue
            notices.append(
                _MaxDeliveryNotice(
                    investigation_id=event.investigation_id,
                    stream_sequence=sequence,
                )
            )
        return tuple(notices)

    async def delete_message(self, stream_sequence: int) -> None:
        try:
            await self._jetstream.delete_msg(self._stream, stream_sequence)
        except NotFoundError:
            pass
        self._pending.discard(stream_sequence)

    async def _capture(self, message: Msg) -> None:
        try:
            body = json.loads(message.data)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("invalid_max_delivery_advisory", exc_info=exc)
            return
        sequence = body.get("stream_seq")
        if isinstance(sequence, int) and sequence > 0:
            self._sequences.put_nowait(sequence)
