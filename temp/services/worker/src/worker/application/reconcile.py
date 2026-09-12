"""Reconcile stalled delivery state and expire historical platform data."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from worker.domain.investigation import InvestigationStatus
from worker.ports.reconciliation_store import (
    ObservedInvestigation,
    ReconciliationMutation,
    ReconciliationRepository,
    ReconciliationUnitOfWork,
    RetentionDeletes,
)


class MaxDeliveryOutcome(StrEnum):
    FAILED = "failed"
    DEFERRED = "deferred"
    ALREADY_TERMINAL = "already_terminal"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class MaxDeliveryDecision:
    investigation_id: uuid.UUID
    outcome: MaxDeliveryOutcome
    mutation: ReconciliationMutation | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    stale_processing: int
    oldest_unpublished_age_seconds: float | None
    max_deliver_exhausted: int
    retention_deletes: RetentionDeletes
    investigations_by_status: dict[str, int]
    investigation_results: tuple[MaxDeliveryDecision, ...]

    @property
    def outbox_lagging(self) -> bool:
        age = self.oldest_unpublished_age_seconds
        return age is not None and age > 300


class Reconcile:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], ReconciliationUnitOfWork],
        database_retention_days: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._retention = timedelta(days=database_retention_days)
        self._clock = clock

    async def execute(self, investigation_ids: tuple[uuid.UUID, ...] = ()) -> ReconciliationResult:
        now = self._clock()
        ordered_ids = tuple(sorted(set(investigation_ids), key=str))
        async with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.reconciliation
            stale = await repository.count_stale_processing(
                expired_before=now - timedelta(minutes=10)
            )
            oldest = await repository.oldest_unpublished_at()
            status_counts = await repository.count_by_status()
            observed = await repository.read_for_update(ordered_ids)
            decisions = await _apply_decisions(repository, ordered_ids, observed)
            deletes = await repository.delete_expired(cutoff=now - self._retention)
            await unit_of_work.commit()

        return ReconciliationResult(
            stale_processing=stale,
            oldest_unpublished_age_seconds=_age_seconds(now, oldest),
            max_deliver_exhausted=sum(
                decision.outcome is MaxDeliveryOutcome.FAILED for decision in decisions
            ),
            retention_deletes=deletes,
            investigations_by_status=status_counts,
            investigation_results=decisions,
        )


async def _apply_decisions(
    repository: ReconciliationRepository,
    ordered_ids: tuple[uuid.UUID, ...],
    observed: tuple[ObservedInvestigation, ...],
) -> tuple[MaxDeliveryDecision, ...]:
    by_id = {state.id: state for state in observed}
    results: list[MaxDeliveryDecision] = []
    for investigation_id in ordered_ids:
        decision = decide_max_delivery(investigation_id, by_id.get(investigation_id))
        if decision.mutation is not None and not await repository.apply_mutation(decision.mutation):
            decision = MaxDeliveryDecision(investigation_id, MaxDeliveryOutcome.DEFERRED)
        results.append(decision)
    return tuple(results)


def decide_max_delivery(
    investigation_id: uuid.UUID,
    state: ObservedInvestigation | None,
) -> MaxDeliveryDecision:
    if state is None:
        return MaxDeliveryDecision(investigation_id, MaxDeliveryOutcome.MISSING)
    if state.status in (InvestigationStatus.COMPLETED, InvestigationStatus.FAILED):
        return MaxDeliveryDecision(investigation_id, MaxDeliveryOutcome.ALREADY_TERMINAL)
    if state.status is InvestigationStatus.PROCESSING and (
        state.lease_expires_at is None or state.lease_expires_at >= state.observed_at
    ):
        return MaxDeliveryDecision(investigation_id, MaxDeliveryOutcome.DEFERRED)
    mutation = ReconciliationMutation(
        investigation_id=investigation_id,
        expected_status=state.status,
        expected_worker_id=state.worker_id,
        expected_lease_expires_at=state.lease_expires_at,
        target_status=InvestigationStatus.FAILED,
        completed_at=state.observed_at,
        error_code="MAX_DELIVER_EXCEEDED",
        error_message="JetStream maximum delivery count was exhausted",
    )
    return MaxDeliveryDecision(investigation_id, MaxDeliveryOutcome.FAILED, mutation)


def _age_seconds(now: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return max(0.0, (now - value).total_seconds())
