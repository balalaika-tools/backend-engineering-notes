"""Accept an investigation batch durably without doing work inline."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from orchestrator.domain.investigation import (
    ExternalInvestigationStatus,
    external_status,
)
from orchestrator.ports.investigation_store import InvestigationUnitOfWork


class InvalidBatchError(ValueError):
    """The batch is empty or contains an invalid identifier."""


class BatchTooLargeError(ValueError):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Batch exceeds the configured limit of {limit}")


@dataclass(frozen=True, slots=True)
class RequestedInvestigation:
    investigation_id: uuid.UUID
    exception_id: str
    status: ExternalInvestigationStatus
    created: bool


@dataclass(frozen=True, slots=True)
class InvestigationBatch:
    request_id: uuid.UUID
    investigations: tuple[RequestedInvestigation, ...]


class RequestInvestigations:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], InvestigationUnitOfWork],
        max_batch_size: int,
        subject: str,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._max_batch_size = max_batch_size
        self._subject = subject

    async def execute(
        self,
        *,
        client_id: str,
        exception_ids: list[str],
        traceparent: str,
    ) -> InvestigationBatch:
        normalized = self._validate_and_deduplicate(exception_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            request_id = await unit_of_work.requests.add(
                client_id=client_id,
                exception_ids=normalized,
            )
            results = []
            for position, exception_id in enumerate(normalized):
                result = await request_one(
                    unit_of_work=unit_of_work,
                    subject=self._subject,
                    request_id=request_id,
                    client_id=client_id,
                    exception_id=exception_id,
                    traceparent=traceparent,
                )
                assert result is not None
                await unit_of_work.requests.attach_investigation(
                    request_id=request_id,
                    investigation_id=result.investigation_id,
                    position=position,
                )
                results.append(result)
            await unit_of_work.commit()
        return InvestigationBatch(request_id=request_id, investigations=tuple(results))

    def _validate_and_deduplicate(self, exception_ids: list[str]) -> list[str]:
        if not exception_ids:
            raise InvalidBatchError("At least one exception ID is required")
        if len(exception_ids) > self._max_batch_size:
            raise BatchTooLargeError(self._max_batch_size)
        normalized = [value.strip() for value in exception_ids]
        if any(not value for value in normalized):
            raise InvalidBatchError("Exception IDs must be non-empty strings")
        return list(dict.fromkeys(normalized))


async def request_one(
    *,
    unit_of_work: InvestigationUnitOfWork,
    subject: str,
    filtered: bool = False,
    request_id: uuid.UUID,
    client_id: str,
    exception_id: str,
    traceparent: str,
) -> RequestedInvestigation | None:
    insert = (
        unit_of_work.investigations.insert_or_attach_filtered
        if filtered
        else unit_of_work.investigations.insert_or_attach
    )
    result = await insert(
        request_id=request_id,
        client_id=client_id,
        exception_id=exception_id,
    )
    if result is None:
        return None
    if result.created:
        await unit_of_work.outbox.add_investigation_requested(
            investigation=result.investigation,
            traceparent=traceparent,
            subject=subject,
        )
    return RequestedInvestigation(
        investigation_id=result.investigation.id,
        exception_id=result.investigation.exception_id,
        status=external_status(result.investigation.status),
        created=result.created,
    )
