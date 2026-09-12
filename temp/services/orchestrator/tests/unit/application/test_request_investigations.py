"""Batch acceptance behavior with a deterministic in-memory unit of work."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import pytest
from orchestrator.application.request_investigations import (
    BatchTooLargeError,
    InvalidBatchError,
    RequestInvestigations,
)
from orchestrator.domain.investigation import (
    InvestigationInsertResult,
    InvestigationRecord,
    InvestigationStatus,
)


@dataclass
class Store:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active: dict[str, InvestigationRecord] = field(default_factory=dict)
    outbox_event_ids: list[uuid.UUID] = field(default_factory=list)
    request_links: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)


class FakeRequests:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def add(self, *, client_id: str, exception_ids: list[str]) -> uuid.UUID:
        del client_id, exception_ids
        request_id = uuid.uuid4()
        self._store.request_links[request_id] = []
        return request_id

    async def attach_investigation(
        self,
        *,
        request_id: uuid.UUID,
        investigation_id: uuid.UUID,
        position: int,
    ) -> None:
        links = self._store.request_links[request_id]
        assert position == len(links)
        links.append(investigation_id)


class FakeInvestigations:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def insert_or_attach(
        self,
        *,
        request_id: uuid.UUID,
        client_id: str,
        exception_id: str,
        investigation_id: uuid.UUID | None = None,
        event_id: uuid.UUID | None = None,
    ) -> InvestigationInsertResult:
        existing = self._store.active.get(exception_id)
        if existing:
            return InvestigationInsertResult(investigation=existing, created=False)
        record = InvestigationRecord(
            id=investigation_id or uuid.uuid4(),
            request_id=request_id,
            client_id=client_id,
            exception_id=exception_id,
            event_id=event_id or uuid.uuid4(),
            status=InvestigationStatus.QUEUED,
            attempt_count=0,
            created_at=datetime.now(UTC),
            started_at=None,
            completed_at=None,
            last_error_code=None,
        )
        self._store.active[exception_id] = record
        return InvestigationInsertResult(investigation=record, created=True)


class FakeOutbox:
    def __init__(self, store: Store) -> None:
        self._store = store

    async def add_investigation_requested(
        self,
        *,
        investigation: InvestigationRecord,
        traceparent: str,
        subject: str,
    ) -> uuid.UUID:
        del traceparent, subject
        self._store.outbox_event_ids.append(investigation.event_id)
        return investigation.event_id


class FakeUnitOfWork:
    def __init__(self, store: Store) -> None:
        self._store = store
        self.requests = FakeRequests(store)
        self.investigations = FakeInvestigations(store)
        self.outbox = FakeOutbox(store)

    async def __aenter__(self) -> Self:
        await self._store.lock.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self._store.lock.release()

    async def commit(self) -> None:
        return None


def _action(store: Store, *, limit: int = 100) -> RequestInvestigations:
    return RequestInvestigations(
        unit_of_work_factory=lambda: FakeUnitOfWork(store),
        max_batch_size=limit,
        subject="investigations.requested",
    )


@pytest.mark.asyncio
async def test_created_and_batch_duplicates_are_collapsed() -> None:
    store = Store()
    result = await _action(store).execute(
        client_id="client",
        exception_ids=["EX-1", "EX-1"],
        traceparent="trace",
    )

    assert len(result.investigations) == 1
    assert result.investigations[0].created is True
    assert len(store.outbox_event_ids) == 1


@pytest.mark.asyncio
async def test_active_investigation_is_attached_without_new_event() -> None:
    store = Store()
    action = _action(store)
    first = await action.execute(client_id="a", exception_ids=["EX-1"], traceparent="trace")
    second = await action.execute(client_id="b", exception_ids=["EX-1"], traceparent="trace")

    assert second.investigations[0].created is False
    assert second.investigations[0].investigation_id == first.investigations[0].investigation_id
    assert store.request_links[first.request_id] == store.request_links[second.request_id]
    assert len(store.outbox_event_ids) == 1


@pytest.mark.asyncio
async def test_terminal_investigation_can_be_rerun() -> None:
    store = Store()
    action = _action(store)
    first = await action.execute(client_id="a", exception_ids=["EX-1"], traceparent="trace")
    store.active.pop("EX-1")
    rerun = await action.execute(client_id="a", exception_ids=["EX-1"], traceparent="trace")

    assert rerun.investigations[0].created is True
    assert rerun.investigations[0].investigation_id != first.investigations[0].investigation_id
    assert len(store.outbox_event_ids) == 2


@pytest.mark.asyncio
async def test_empty_and_oversized_batches_are_rejected() -> None:
    action = _action(Store(), limit=1)
    with pytest.raises(InvalidBatchError):
        await action.execute(client_id="a", exception_ids=[], traceparent="trace")
    with pytest.raises(InvalidBatchError):
        await action.execute(client_id="a", exception_ids=["  "], traceparent="trace")
    with pytest.raises(BatchTooLargeError) as error:
        await action.execute(client_id="a", exception_ids=["EX-1", "EX-2"], traceparent="trace")
    assert error.value.limit == 1


@pytest.mark.asyncio
async def test_concurrent_identical_requests_create_one_investigation() -> None:
    store = Store()
    action = _action(store)
    first, second = await asyncio.gather(
        action.execute(client_id="a", exception_ids=["EX-1"], traceparent="trace"),
        action.execute(client_id="b", exception_ids=["EX-1"], traceparent="trace"),
    )

    assert sorted(item.investigations[0].created for item in (first, second)) == [False, True]
    assert len(store.active) == 1
    assert len(store.outbox_event_ids) == 1
