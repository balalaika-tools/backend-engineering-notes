"""Status query behavior at the application boundary."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import pytest
from orchestrator.application.query_status import (
    InvalidStatusQueryError,
    QueryStatus,
    StatusBatchTooLargeError,
)
from orchestrator.domain.investigation import (
    ExternalInvestigationStatus,
    InvestigationRecord,
    InvestigationStatus,
)

FIRST_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SECOND_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _record(
    investigation_id: uuid.UUID,
    exception_id: str,
    *,
    status: InvestigationStatus,
    attempt_count: int,
) -> InvestigationRecord:
    return InvestigationRecord(
        id=investigation_id,
        request_id=uuid.uuid4(),
        client_id="client",
        exception_id=exception_id,
        event_id=uuid.uuid4(),
        status=status,
        attempt_count=attempt_count,
        created_at=datetime.now(UTC),
        started_at=None,
        completed_at=None,
        last_error_code=None,
    )


@dataclass
class FakeReader:
    records: tuple[InvestigationRecord, ...]

    async def by_ids(
        self, investigation_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, InvestigationRecord]:
        requested = set(investigation_ids)
        return {record.id: record for record in self.records if record.id in requested}

    async def most_recent_by_exception_ids(
        self, exception_ids: list[str]
    ) -> dict[str, InvestigationRecord]:
        requested = set(exception_ids)
        return {
            record.exception_id: record
            for record in self.records
            if record.exception_id in requested
        }


@dataclass
class FakeUnitOfWork:
    investigations: FakeReader

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def _action(*records: InvestigationRecord, limit: int = 100) -> QueryStatus:
    return QueryStatus(
        unit_of_work_factory=lambda: FakeUnitOfWork(FakeReader(records)),
        max_batch_size=limit,
    )


@pytest.mark.asyncio
async def test_queued_and_processing_are_both_externally_in_progress() -> None:
    action = _action(
        _record(FIRST_ID, "EX-1", status=InvestigationStatus.QUEUED, attempt_count=0),
        _record(SECOND_ID, "EX-2", status=InvestigationStatus.PROCESSING, attempt_count=1),
    )

    result = await action.execute(
        exception_ids=["EX-1", "EX-2"],
        investigation_ids=None,
    )

    assert [item.status for item in result.investigations] == [
        ExternalInvestigationStatus.IN_PROGRESS,
        ExternalInvestigationStatus.IN_PROGRESS,
    ]


@pytest.mark.asyncio
async def test_retry_attempt_count_remains_visible_while_queued() -> None:
    action = _action(_record(FIRST_ID, "EX-1", status=InvestigationStatus.QUEUED, attempt_count=2))

    result = await action.execute(exception_ids=None, investigation_ids=[FIRST_ID])

    assert result.investigations[0].status is ExternalInvestigationStatus.IN_PROGRESS
    assert result.investigations[0].attempt_count == 2


@pytest.mark.asyncio
async def test_unknown_identifiers_are_returned_as_not_found() -> None:
    unknown_investigation_id = uuid.uuid4()
    by_exception = await _action().execute(
        exception_ids=["UNKNOWN"],
        investigation_ids=None,
    )
    by_investigation = await _action().execute(
        exception_ids=None,
        investigation_ids=[unknown_investigation_id],
    )

    assert by_exception.investigations[0].status is ExternalInvestigationStatus.NOT_FOUND
    assert by_exception.investigations[0].exception_id == "UNKNOWN"
    assert by_investigation.investigations[0].status is ExternalInvestigationStatus.NOT_FOUND
    assert by_investigation.investigations[0].investigation_id == unknown_investigation_id


@pytest.mark.asyncio
async def test_status_query_requires_one_identifier_kind_within_limit() -> None:
    action = _action(limit=1)
    with pytest.raises(InvalidStatusQueryError):
        await action.execute(exception_ids=None, investigation_ids=None)
    with pytest.raises(InvalidStatusQueryError):
        await action.execute(exception_ids=["EX-1"], investigation_ids=[FIRST_ID])
    with pytest.raises(InvalidStatusQueryError):
        await action.execute(exception_ids=[], investigation_ids=None)
    with pytest.raises(StatusBatchTooLargeError):
        await action.execute(exception_ids=["EX-1", "EX-2"], investigation_ids=None)
