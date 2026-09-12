"""Durable event records awaiting publication."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: int
    event_id: uuid.UUID
    subject: str
    payload: Mapping[str, object]
    attempt_count: int
