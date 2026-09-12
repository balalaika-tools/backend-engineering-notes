"""Transactional persistence contract for investigation execution."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Literal, Protocol, Self

from worker.domain.investigation import InvestigationState, InvestigationStatus

WriteBackStep = Literal["comment", "codes"]


@dataclass(frozen=True, slots=True)
class InvestigationFailureMutation:
    investigation_id: uuid.UUID
    worker_id: str
    expected_attempt_count: int
    target_status: InvestigationStatus
    completed_at: datetime | None
    error_code: str
    error_message: str


class InvestigationStoreUnavailableError(RuntimeError):
    """Investigation persistence may succeed when the work is retried."""

    error_code = "platform_database_unavailable"


class InvestigationRepository(Protocol):
    async def get(self, investigation_id: uuid.UUID) -> InvestigationState | None: ...

    async def persist_analysis(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        analysis: Mapping[str, object],
        report: str | None,
        report_uri: str,
    ) -> bool: ...

    async def record_write_back_step(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        step: WriteBackStep,
        outcome: str,
        detail: str | None = None,
    ) -> bool: ...

    async def complete(self, investigation_id: uuid.UUID, *, worker_id: str) -> bool: ...

    async def apply_failure_transition(self, mutation: InvestigationFailureMutation) -> bool: ...


class InvestigationUnitOfWork(Protocol):
    @property
    def investigations(self) -> InvestigationRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
