"""Application-owned durable failure classification and transition policy."""

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Self, cast

import pytest
from worker.application.record_investigation_failure import (
    ClassifiedInvestigationFailure,
    FailureOutcome,
    RecordInvestigationFailure,
    classify_investigation_failure,
)
from worker.domain.investigation import (
    InvestigationExecutionError,
    InvestigationState,
    InvestigationStatus,
    PermanentInvestigationError,
    TransientInvestigationError,
)
from worker.ports.investigation.investigation_store import (
    InvestigationFailureMutation,
    InvestigationUnitOfWork,
)

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
INVESTIGATION_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _state(*, attempts: int = 1, worker_id: str = "worker-a") -> InvestigationState:
    return InvestigationState(
        id=INVESTIGATION_ID,
        request_id=uuid.uuid4(),
        exception_id="EX-1",
        status=InvestigationStatus.PROCESSING,
        attempt_count=attempts,
        worker_id=worker_id,
        lease_expires_at=NOW,
        last_heartbeat_at=NOW,
        started_at=NOW,
        completed_at=None,
        last_error_code=None,
        analysis=None,
        analysis_persisted_at=None,
        report=None,
        report_uri=None,
        comment_outcome=None,
        comment_detail=None,
        codes_outcome=None,
        codes_detail=None,
    )


class FakeRepository:
    def __init__(self, state: InvestigationState | None, *, applies: bool = True) -> None:
        self.state = state
        self.applies = applies
        self.mutations: list[InvestigationFailureMutation] = []

    async def get(self, investigation_id: uuid.UUID) -> InvestigationState | None:
        assert investigation_id == INVESTIGATION_ID
        return self.state

    async def apply_failure_transition(self, mutation: InvestigationFailureMutation) -> bool:
        self.mutations.append(mutation)
        if not self.applies or self.state is None:
            return False
        self.state = replace(
            self.state,
            status=mutation.target_status,
            worker_id=None,
            lease_expires_at=None,
            completed_at=mutation.completed_at,
            last_error_code=mutation.error_code,
        )
        return True


class FakeUnitOfWork:
    def __init__(self, repository: FakeRepository) -> None:
        self.investigations = repository
        self.commits = 0

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
        self.commits += 1


def _action(
    repository: FakeRepository, *, max_attempts: int = 5
) -> tuple[RecordInvestigationFailure, FakeUnitOfWork]:
    unit_of_work = FakeUnitOfWork(repository)
    return (
        RecordInvestigationFailure(
            unit_of_work_factory=cast(
                Callable[[], InvestigationUnitOfWork],
                lambda: unit_of_work,
            ),
            max_attempts=max_attempts,
            clock=lambda: NOW,
        ),
        unit_of_work,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attempts", "permanent", "expected"),
    [
        pytest.param(2, False, FailureOutcome.RETRY, id="transient-budget-remaining"),
        pytest.param(5, False, FailureOutcome.FAILED, id="transient-budget-exhausted"),
        pytest.param(1, True, FailureOutcome.FAILED, id="permanent-first-attempt"),
    ],
)
async def test_failure_policy_selects_and_commits_transition(
    attempts: int,
    permanent: bool,
    expected: FailureOutcome,
) -> None:
    repository = FakeRepository(_state(attempts=attempts))
    action, unit_of_work = _action(repository)

    result = await action.execute(
        investigation_id=INVESTIGATION_ID,
        worker_id="worker-a",
        failure=ClassifiedInvestigationFailure("failure_code", "failed", permanent),
    )

    assert result.outcome is expected
    assert unit_of_work.commits == 1
    assert repository.mutations[0].target_status is (
        InvestigationStatus.FAILED
        if expected is FailureOutcome.FAILED
        else InvestigationStatus.QUEUED
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, _state(worker_id="worker-other")])
async def test_missing_or_changed_owner_is_not_mutated(state: InvestigationState | None) -> None:
    repository = FakeRepository(state)
    action, unit_of_work = _action(repository)

    result = await action.execute(
        investigation_id=INVESTIGATION_ID,
        worker_id="worker-a",
        failure=ClassifiedInvestigationFailure("failure_code", "failed", False),
    )

    assert result.outcome is FailureOutcome.OWNERSHIP_LOST
    assert repository.mutations == []
    assert unit_of_work.commits == 0


@pytest.mark.asyncio
async def test_concurrent_ownership_change_during_update_is_reported() -> None:
    repository = FakeRepository(_state(), applies=False)
    action, unit_of_work = _action(repository)

    result = await action.execute(
        investigation_id=INVESTIGATION_ID,
        worker_id="worker-a",
        failure=ClassifiedInvestigationFailure("failure_code", "failed", False),
    )

    assert result.outcome is FailureOutcome.OWNERSHIP_LOST
    assert len(repository.mutations) == 1
    assert unit_of_work.commits == 0


def test_unknown_execution_failure_is_transient_internal_error() -> None:
    result = classify_investigation_failure(ValueError("unexpected"))

    assert result == ClassifiedInvestigationFailure("internal_error", "unexpected", False)


@pytest.mark.parametrize(
    ("error", "permanent"),
    [
        (TransientInvestigationError("down", error_code="provider_down"), False),
        (PermanentInvestigationError("invalid", error_code="invalid_payload"), True),
    ],
)
def test_known_execution_failure_preserves_classification(
    error: InvestigationExecutionError,
    permanent: bool,
) -> None:
    result = classify_investigation_failure(error)

    assert result.error_code == error.error_code
    assert result.permanent is permanent


@pytest.mark.asyncio
async def test_shutdown_interruption_requeues_even_at_attempt_limit() -> None:
    repository = FakeRepository(_state(attempts=5))
    action, _ = _action(repository)

    result = await action.release_interrupted(
        investigation_id=INVESTIGATION_ID,
        worker_id="worker-a",
    )

    assert result.outcome is FailureOutcome.RETRY
    assert repository.mutations[0].target_status is InvestigationStatus.QUEUED
    assert repository.mutations[0].error_code == "shutdown_interrupted"
