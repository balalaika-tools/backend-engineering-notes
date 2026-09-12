"""Reliable event-publishing boundary."""

import uuid
from collections.abc import Mapping
from typing import Protocol


class EventPublishError(RuntimeError):
    """The broker did not durably acknowledge an event."""


class EventPublisher(Protocol):
    async def publish(
        self,
        *,
        subject: str,
        payload: Mapping[str, object],
        message_id: uuid.UUID,
    ) -> None: ...
