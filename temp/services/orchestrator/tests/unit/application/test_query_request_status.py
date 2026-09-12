"""Investigation request status behavior at the application boundary."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import pytest
from orchestrator.application.query_status import (
    InvestigationRequestNotFoundError,
    QueryInvestigationRequestStatus,
    RequestStatusSummary,
)
from orchestrator.domain.investigation import InvestigationRecord, InvestigationStatus

REQUEST_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")


def _record(
    value: int,
    *,
    status: InvestigationStatus,
) -> InvestigationRecord:
    return InvestigationRecord(
        id=uuid.UUID(int=value),
        request_id=REQUEST_ID,
        client_id="client-a",
        exception_id=f"EX-{value}",
        event_id=uuid.uuid4(),
        status=status,
        attempt_count=1,
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        started_at=None,
        completed_at=None,
        last_error_code=None,
    )


@dataclass
class FakeRequests:
    owner: str

    async def exists_for_client(self, *, request_id: uuid.UUID, client_id: str) -> bool:
        return request_id == REQUEST_ID and client_id == self.owner


@dataclass
class FakeInvestigations:
    records: list[InvestigationRecord]

    async def by_api_request_id(self, request_id: uuid.UUID) -> list[InvestigationRecord]:
        assert request_id == REQUEST_ID
        return self.records


@dataclass
class FakeUnitOfWork:
    requests: FakeRequests
    investigations: FakeInvestigations

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def _action(*records: InvestigationRecord) -> QueryInvestigationRequestStatus:
    return QueryInvestigationRequestStatus(
        unit_of_work_factory=lambda: FakeUnitOfWork(
            requests=FakeRequests(owner="client-a"),
            investigations=FakeInvestigations(records=list(records)),
        )
    )


@pytest.mark.asyncio
async def test_batch_status_preserves_membership_order_and_summarizes_progress() -> None:
    result = await _action(
        _record(1, status=InvestigationStatus.COMPLETED),
        _record(2, status=InvestigationStatus.FAILED),
        _record(3, status=InvestigationStatus.PROCESSING),
    ).execute(request_id=REQUEST_ID, client_id="client-a")

    assert [item.exception_id for item in result.investigations] == ["EX-1", "EX-2", "EX-3"]
    assert result.status == "in_progress"
    assert result.summary.total == 3
    assert result.summary.in_progress == 1
    assert result.summary.completed == 1
    assert result.summary.failed == 1


@pytest.mark.asyncio
async def test_terminal_batch_is_failed_when_any_member_failed() -> None:
    result = await _action(
        _record(1, status=InvestigationStatus.COMPLETED),
        _record(2, status=InvestigationStatus.FAILED),
    ).execute(request_id=REQUEST_ID, client_id="client-a")

    assert result.status == "failed"


@pytest.mark.asyncio
async def test_empty_request_is_completed_with_zero_summary() -> None:
    result = await _action().execute(request_id=REQUEST_ID, client_id="client-a")

    assert result.status == "completed"
    assert result.investigations == ()
    assert result.summary == RequestStatusSummary(
        total=0,
        in_progress=0,
        completed=0,
        failed=0,
    )


@pytest.mark.asyncio
async def test_request_is_not_visible_to_another_client() -> None:
    with pytest.raises(InvestigationRequestNotFoundError):
        await _action(_record(1, status=InvestigationStatus.QUEUED)).execute(
            request_id=REQUEST_ID,
            client_id="client-b",
        )
