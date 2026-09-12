"""Application-owned reconciliation policy and transaction orchestration."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self

import pytest
from worker.application.reconcile import MaxDeliveryOutcome, Reconcile, decide_max_delivery
from worker.domain.investigation import InvestigationStatus
from worker.ports.reconciliation_store import (
    ObservedInvestigation,
    ReconciliationMutation,
    ReconciliationStoreUnavailableError,
    RetentionDeletes,
)

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def _observed(
    investigation_id: uuid.UUID,
    *,
    status: InvestigationStatus,
    lease_expires_at: datetime | None = None,
) -> ObservedInvestigation:
    return ObservedInvestigation(
        id=investigation_id,
        status=status,
        worker_id="worker-a" if status is InvestigationStatus.PROCESSING else None,
        lease_expires_at=lease_expires_at,
        observed_at=NOW,
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        pytest.param(None, MaxDeliveryOutcome.MISSING, id="missing"),
        pytest.param(
            _observed(uuid.uuid4(), status=InvestigationStatus.QUEUED),
            MaxDeliveryOutcome.FAILED,
            id="queued",
        ),
        pytest.param(
            _observed(
                uuid.uuid4(),
                status=InvestigationStatus.PROCESSING,
                lease_expires_at=NOW - timedelta(seconds=1),
            ),
            MaxDeliveryOutcome.FAILED,
            id="expired-owner",
        ),
        pytest.param(
            _observed(
                uuid.uuid4(),
                status=InvestigationStatus.PROCESSING,
                lease_expires_at=NOW + timedelta(seconds=1),
            ),
            MaxDeliveryOutcome.DEFERRED,
            id="live-owner",
        ),
        pytest.param(
            _observed(uuid.uuid4(), status=InvestigationStatus.COMPLETED),
            MaxDeliveryOutcome.ALREADY_TERMINAL,
            id="completed",
        ),
        pytest.param(
            _observed(uuid.uuid4(), status=InvestigationStatus.FAILED),
            MaxDeliveryOutcome.ALREADY_TERMINAL,
            id="failed",
        ),
    ],
)
def test_max_delivery_policy_selects_business_outcome(
    state: ObservedInvestigation | None,
    expected: MaxDeliveryOutcome,
) -> None:
    investigation_id = state.id if state is not None else uuid.uuid4()

    decision = decide_max_delivery(investigation_id, state)

    assert decision.outcome is expected
    assert (decision.mutation is not None) is (expected is MaxDeliveryOutcome.FAILED)


@dataclass
class FakeRepository:
    observed: tuple[ObservedInvestigation, ...]
    events: list[str]
    oldest: datetime | None = NOW - timedelta(minutes=6)
    mutation_result: bool = True
    mutations: list[ReconciliationMutation] = field(default_factory=list)
    requested_ids: tuple[uuid.UUID, ...] = ()
    stale_cutoff: datetime | None = None
    retention_cutoff: datetime | None = None

    async def count_stale_processing(self, *, expired_before: datetime) -> int:
        self.stale_cutoff = expired_before
        return 2

    async def count_by_status(self) -> dict[str, int]:
        return {"processing": 2}

    async def oldest_unpublished_at(self) -> datetime | None:
        return self.oldest

    async def read_for_update(
        self, investigation_ids: tuple[uuid.UUID, ...]
    ) -> tuple[ObservedInvestigation, ...]:
        self.requested_ids = investigation_ids
        return self.observed

    async def apply_mutation(self, mutation: ReconciliationMutation) -> bool:
        self.mutations.append(mutation)
        return self.mutation_result

    async def delete_expired(self, *, cutoff: datetime) -> RetentionDeletes:
        self.retention_cutoff = cutoff
        return RetentionDeletes(outbox_events=3, investigations=2, api_requests=1)


class FakeUnitOfWork:
    def __init__(
        self,
        repository: FakeRepository,
        events: list[str],
        *,
        fail_commit: bool = False,
    ) -> None:
        self.reconciliation = repository
        self._events = events
        self._fail_commit = fail_commit

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
        self._events.append("commit")
        if self._fail_commit:
            raise ReconciliationStoreUnavailableError("database unavailable")


@pytest.mark.asyncio
async def test_reconcile_deduplicates_ids_commits_decisions_and_preserves_cutoffs() -> None:
    events: list[str] = []
    investigation_id = uuid.uuid4()
    repository = FakeRepository(
        (_observed(investigation_id, status=InvestigationStatus.QUEUED),), events
    )
    action = Reconcile(
        unit_of_work_factory=lambda: FakeUnitOfWork(repository, events),
        database_retention_days=90,
        clock=lambda: NOW,
    )

    result = await action.execute((investigation_id, investigation_id))

    assert repository.requested_ids == (investigation_id,)
    assert len(repository.mutations) == 1
    assert events == ["commit"]
    assert repository.stale_cutoff == NOW - timedelta(minutes=10)
    assert repository.retention_cutoff == NOW - timedelta(days=90)
    assert result.max_deliver_exhausted == 1
    assert result.investigation_results[0].outcome is MaxDeliveryOutcome.FAILED
    assert result.outbox_lagging


@pytest.mark.asyncio
async def test_conditional_mutation_conflict_is_deferred() -> None:
    investigation_id = uuid.uuid4()
    repository = FakeRepository(
        (_observed(investigation_id, status=InvestigationStatus.QUEUED),),
        [],
        mutation_result=False,
    )
    action = Reconcile(
        unit_of_work_factory=lambda: FakeUnitOfWork(repository, []),
        database_retention_days=90,
        clock=lambda: NOW,
    )

    result = await action.execute((investigation_id,))

    assert result.max_deliver_exhausted == 0
    assert result.investigation_results[0].outcome is MaxDeliveryOutcome.DEFERRED


@pytest.mark.asyncio
async def test_failed_commit_does_not_return_uncommitted_outcomes() -> None:
    investigation_id = uuid.uuid4()
    repository = FakeRepository(
        (_observed(investigation_id, status=InvestigationStatus.QUEUED),), []
    )
    action = Reconcile(
        unit_of_work_factory=lambda: FakeUnitOfWork(repository, [], fail_commit=True),
        database_retention_days=90,
        clock=lambda: NOW,
    )

    with pytest.raises(ReconciliationStoreUnavailableError, match="database unavailable"):
        await action.execute((investigation_id,))
