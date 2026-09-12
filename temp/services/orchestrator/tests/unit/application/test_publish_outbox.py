"""Transactional outbox publishing decisions."""

import uuid
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

import pytest
from orchestrator.application.publish_outbox import PublishOutbox
from orchestrator.domain.outbox import OutboxRecord
from orchestrator.ports.event_publisher import EventPublishError


def _event(row_id: int, *, attempt_count: int = 0) -> OutboxRecord:
    return OutboxRecord(
        id=row_id,
        event_id=uuid.uuid4(),
        subject="investigations.requested",
        payload={"investigation_id": str(uuid.uuid4())},
        attempt_count=attempt_count,
    )


@dataclass
class Store:
    events: list[OutboxRecord]
    published: list[int] = field(default_factory=list)
    failed: list[tuple[int, str, float]] = field(default_factory=list)
    committed: bool = False


@dataclass
class FakeOutbox:
    store: Store

    async def claim_pending(self, *, limit: int) -> list[OutboxRecord]:
        return self.store.events[:limit]

    async def count_pending(self) -> int:
        published = set(self.store.published)
        return sum(event.id not in published for event in self.store.events)

    async def mark_published(self, outbox_id: int) -> None:
        self.store.published.append(outbox_id)

    async def mark_failed(
        self,
        outbox_id: int,
        *,
        error: str,
        retry_after_seconds: float,
    ) -> None:
        self.store.failed.append((outbox_id, error, retry_after_seconds))


@dataclass
class FakeUnitOfWork:
    store: Store

    def __post_init__(self) -> None:
        self.outbox = FakeOutbox(self.store)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        self.store.committed = True


@dataclass
class FakePublisher:
    failing_ids: set[uuid.UUID] = field(default_factory=set)
    published_ids: list[uuid.UUID] = field(default_factory=list)

    async def publish(
        self,
        *,
        subject: str,
        payload: object,
        message_id: uuid.UUID,
    ) -> None:
        del subject, payload
        if message_id in self.failing_ids:
            raise EventPublishError("broker timeout")
        self.published_ids.append(message_id)


def _action(store: Store, publisher: FakePublisher, *, batch_size: int = 100) -> PublishOutbox:
    return PublishOutbox(
        unit_of_work_factory=lambda: FakeUnitOfWork(store),
        publisher=publisher,
        batch_size=batch_size,
    )


@pytest.mark.asyncio
async def test_acknowledged_events_are_marked_published_and_committed() -> None:
    events = [_event(1), _event(2)]
    store = Store(events=events)
    publisher = FakePublisher()

    result = await _action(store, publisher).execute()

    assert result.published == 2
    assert result.failed == 0
    assert store.published == [1, 2]
    assert publisher.published_ids == [event.event_id for event in events]
    assert store.committed is True


@pytest.mark.asyncio
async def test_publish_failure_stays_pending_with_capped_backoff() -> None:
    event = _event(1, attempt_count=20)
    store = Store(events=[event])
    publisher = FakePublisher(failing_ids={event.event_id})

    result = await _action(store, publisher).execute()

    assert result.published == 0
    assert result.failed == 1
    assert store.published == []
    assert store.failed == [(1, "broker timeout", 60.0)]
    assert store.committed is True


@pytest.mark.asyncio
async def test_batch_size_limits_rows_claimed_per_transaction() -> None:
    store = Store(events=[_event(1), _event(2)])
    publisher = FakePublisher()

    result = await _action(store, publisher, batch_size=1).execute()

    assert result.selected == 1
    assert store.published == [1]
