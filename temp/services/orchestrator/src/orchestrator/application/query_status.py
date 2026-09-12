"""Query externally visible investigation status without exposing worker states."""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from orchestrator.domain.investigation import (
    ExternalInvestigationStatus,
    InvestigationRecord,
    external_status,
)
from orchestrator.ports.investigation_store import (
    RequestStatusQueryUnitOfWork,
    StatusQueryUnitOfWork,
)

AggregateRequestStatus = Literal["in_progress", "completed", "failed"]


class InvalidStatusQueryError(ValueError):
    """The query does not select exactly one non-empty identifier kind."""


class StatusBatchTooLargeError(ValueError):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Batch exceeds the configured limit of {limit}")


@dataclass(frozen=True, slots=True)
class InvestigationStatusItem:
    investigation_id: uuid.UUID | None
    exception_id: str | None
    status: ExternalInvestigationStatus
    attempt_count: int | None
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class InvestigationStatusBatch:
    investigations: tuple[InvestigationStatusItem, ...]


@dataclass(frozen=True, slots=True)
class RequestStatusSummary:
    total: int
    in_progress: int
    completed: int
    failed: int


@dataclass(frozen=True, slots=True)
class InvestigationRequestStatus:
    request_id: uuid.UUID
    status: AggregateRequestStatus
    summary: RequestStatusSummary
    investigations: tuple[InvestigationStatusItem, ...]


class InvestigationRequestNotFoundError(LookupError):
    """The request does not exist for the authenticated client."""


class QueryStatus:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], StatusQueryUnitOfWork],
        max_batch_size: int,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._max_batch_size = max_batch_size

    async def execute(
        self,
        *,
        exception_ids: list[str] | None,
        investigation_ids: list[uuid.UUID] | None,
    ) -> InvestigationStatusBatch:
        if (exception_ids is None) == (investigation_ids is None):
            raise InvalidStatusQueryError(
                "Provide exactly one of exception_ids or investigation_ids"
            )
        if exception_ids is not None:
            values = self._validate_exception_ids(exception_ids)
            async with self._unit_of_work_factory() as unit_of_work:
                found = await unit_of_work.investigations.most_recent_by_exception_ids(values)
            return InvestigationStatusBatch(
                investigations=tuple(
                    _item(found.get(exception_id), exception_id=exception_id)
                    for exception_id in values
                )
            )

        assert investigation_ids is not None
        self._validate_size(investigation_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            found_by_id = await unit_of_work.investigations.by_ids(investigation_ids)
        return InvestigationStatusBatch(
            investigations=tuple(
                _item(found_by_id.get(investigation_id), investigation_id=investigation_id)
                for investigation_id in investigation_ids
            )
        )

    def _validate_exception_ids(self, exception_ids: list[str]) -> list[str]:
        self._validate_size(exception_ids)
        normalized = [value.strip() for value in exception_ids]
        if any(not value for value in normalized):
            raise InvalidStatusQueryError("Exception IDs must be non-empty strings")
        return normalized

    def _validate_size(self, values: Sequence[object]) -> None:
        if not values:
            raise InvalidStatusQueryError("At least one identifier is required")
        if len(values) > self._max_batch_size:
            raise StatusBatchTooLargeError(self._max_batch_size)


class QueryInvestigationRequestStatus:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], RequestStatusQueryUnitOfWork],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(
        self,
        *,
        request_id: uuid.UUID,
        client_id: str,
    ) -> InvestigationRequestStatus:
        async with self._unit_of_work_factory() as unit_of_work:
            exists = await unit_of_work.requests.exists_for_client(
                request_id=request_id,
                client_id=client_id,
            )
            if not exists:
                raise InvestigationRequestNotFoundError(str(request_id))
            records = await unit_of_work.investigations.by_api_request_id(request_id)

        investigations = tuple(_item(record) for record in records)
        summary = _summarize(investigations)
        return InvestigationRequestStatus(
            request_id=request_id,
            status=_aggregate_status(summary),
            summary=summary,
            investigations=investigations,
        )


def _item(
    record: InvestigationRecord | None,
    *,
    investigation_id: uuid.UUID | None = None,
    exception_id: str | None = None,
) -> InvestigationStatusItem:
    if record is None:
        return InvestigationStatusItem(
            investigation_id=investigation_id,
            exception_id=exception_id,
            status=ExternalInvestigationStatus.NOT_FOUND,
            attempt_count=None,
            created_at=None,
            started_at=None,
            completed_at=None,
            last_error_code=None,
        )
    return InvestigationStatusItem(
        investigation_id=record.id,
        exception_id=record.exception_id,
        status=external_status(record.status),
        attempt_count=record.attempt_count,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        last_error_code=record.last_error_code,
    )


def _summarize(
    investigations: tuple[InvestigationStatusItem, ...],
) -> RequestStatusSummary:
    counts = {status: 0 for status in ExternalInvestigationStatus}
    for investigation in investigations:
        counts[investigation.status] += 1
    return RequestStatusSummary(
        total=len(investigations),
        in_progress=counts[ExternalInvestigationStatus.IN_PROGRESS],
        completed=counts[ExternalInvestigationStatus.COMPLETED],
        failed=counts[ExternalInvestigationStatus.FAILED],
    )


def _aggregate_status(summary: RequestStatusSummary) -> AggregateRequestStatus:
    if summary.in_progress:
        return "in_progress"
    if summary.failed:
        return "failed"
    return "completed"
