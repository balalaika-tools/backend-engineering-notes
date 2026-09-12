"""Transactional persistence contract for reconciliation passes."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from worker.domain.investigation import InvestigationStatus


class ReconciliationStoreUnavailableError(RuntimeError):
    """Reconciliation persistence is temporarily unavailable."""


@dataclass(frozen=True, slots=True)
class ObservedInvestigation:
    id: uuid.UUID
    status: InvestigationStatus
    worker_id: str | None
    lease_expires_at: datetime | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ReconciliationMutation:
    investigation_id: uuid.UUID
    expected_status: InvestigationStatus
    expected_worker_id: str | None
    expected_lease_expires_at: datetime | None
    target_status: InvestigationStatus
    completed_at: datetime
    error_code: str
    error_message: str


@dataclass(frozen=True, slots=True)
class RetentionDeletes:
    outbox_events: int = 0
    investigations: int = 0
    api_requests: int = 0


class ReconciliationRepository(Protocol):
    async def count_by_status(self) -> dict[str, int]: ...

    async def count_stale_processing(self, *, expired_before: datetime) -> int: ...

    async def oldest_unpublished_at(self) -> datetime | None: ...

    async def read_for_update(
        self, investigation_ids: tuple[uuid.UUID, ...]
    ) -> tuple[ObservedInvestigation, ...]: ...

    async def apply_mutation(self, mutation: ReconciliationMutation) -> bool: ...

    async def delete_expired(self, *, cutoff: datetime) -> RetentionDeletes: ...


class ReconciliationUnitOfWork(Protocol):
    @property
    def reconciliation(self) -> ReconciliationRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
