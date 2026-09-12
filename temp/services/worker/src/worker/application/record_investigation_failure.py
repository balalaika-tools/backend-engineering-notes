"""Select and commit retry or terminal investigation failure transitions."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from worker.domain.investigation import (
    InvestigationExecutionError,
    InvestigationStatus,
    PermanentInvestigationError,
)
from worker.ports.investigation.investigation_store import (
    InvestigationFailureMutation,
    InvestigationUnitOfWork,
)


@dataclass(frozen=True, slots=True)
class ClassifiedInvestigationFailure:
    error_code: str
    message: str
    permanent: bool


class FailureOutcome(StrEnum):
    RETRY = "retry"
    FAILED = "failed"
    OWNERSHIP_LOST = "ownership_lost"


@dataclass(frozen=True, slots=True)
class RecordedFailure:
    outcome: FailureOutcome
    attempt_count: int | None


class RecordInvestigationFailure:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], InvestigationUnitOfWork],
        max_attempts: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._max_attempts = max_attempts
        self._clock = clock

    async def execute(
        self,
        *,
        investigation_id: uuid.UUID,
        worker_id: str,
        failure: ClassifiedInvestigationFailure,
    ) -> RecordedFailure:
        async with self._unit_of_work_factory() as unit_of_work:
            state = await unit_of_work.investigations.get(investigation_id)
            if (
                state is None
                or state.status is not InvestigationStatus.PROCESSING
                or state.worker_id != worker_id
            ):
                return RecordedFailure(FailureOutcome.OWNERSHIP_LOST, None)
            target = (
                InvestigationStatus.FAILED
                if failure.permanent or state.attempt_count >= self._max_attempts
                else InvestigationStatus.QUEUED
            )
            mutation = InvestigationFailureMutation(
                investigation_id=investigation_id,
                worker_id=worker_id,
                expected_attempt_count=state.attempt_count,
                target_status=target,
                completed_at=self._clock() if target is InvestigationStatus.FAILED else None,
                error_code=failure.error_code,
                error_message=failure.message,
            )
            if not await unit_of_work.investigations.apply_failure_transition(mutation):
                return RecordedFailure(FailureOutcome.OWNERSHIP_LOST, state.attempt_count)
            await unit_of_work.commit()
        outcome = (
            FailureOutcome.FAILED if target is InvestigationStatus.FAILED else FailureOutcome.RETRY
        )
        return RecordedFailure(outcome, state.attempt_count)

    async def release_interrupted(
        self,
        *,
        investigation_id: uuid.UUID,
        worker_id: str,
    ) -> RecordedFailure:
        failure = ClassifiedInvestigationFailure(
            error_code="shutdown_interrupted",
            message="Worker shutdown grace period expired",
            permanent=False,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            state = await unit_of_work.investigations.get(investigation_id)
            if (
                state is None
                or state.status is not InvestigationStatus.PROCESSING
                or state.worker_id != worker_id
            ):
                return RecordedFailure(FailureOutcome.OWNERSHIP_LOST, None)
            mutation = InvestigationFailureMutation(
                investigation_id=investigation_id,
                worker_id=worker_id,
                expected_attempt_count=state.attempt_count,
                target_status=InvestigationStatus.QUEUED,
                completed_at=None,
                error_code=failure.error_code,
                error_message=failure.message,
            )
            if not await unit_of_work.investigations.apply_failure_transition(mutation):
                return RecordedFailure(FailureOutcome.OWNERSHIP_LOST, state.attempt_count)
            await unit_of_work.commit()
        return RecordedFailure(FailureOutcome.RETRY, state.attempt_count)


def classify_investigation_failure(error: Exception) -> ClassifiedInvestigationFailure:
    if isinstance(error, InvestigationExecutionError):
        return ClassifiedInvestigationFailure(
            error_code=error.error_code,
            message=str(error),
            permanent=isinstance(error, PermanentInvestigationError),
        )
    return ClassifiedInvestigationFailure(
        error_code="internal_error",
        message=str(error),
        permanent=False,
    )
