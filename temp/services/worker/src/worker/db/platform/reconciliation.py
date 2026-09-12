"""Platform-database checks and retention deletes owned by the reconciler."""

import uuid
from datetime import datetime
from typing import cast

from platform_db import ApiRequest, Investigation, InvestigationStatus, OutboxEvent
from sqlalchemy import Table, delete, exists, func, select, update
from sqlmodel.ext.asyncio.session import AsyncSession
from worker.domain.investigation import InvestigationStatus as DomainInvestigationStatus
from worker.ports.reconciliation_store import (
    ObservedInvestigation,
    ReconciliationMutation,
    RetentionDeletes,
)


class ReconciliationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_stale_processing(self, *, expired_before: datetime) -> int:
        table = _table(Investigation)
        statement = (
            select(func.count())
            .select_from(table)
            .where(
                table.c.status == InvestigationStatus.PROCESSING,
                table.c.lease_expires_at < expired_before,
            )
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def count_by_status(self) -> dict[str, int]:
        table = _table(Investigation)
        rows = await self._session.execute(
            select(table.c.status, func.count()).group_by(table.c.status)
        )
        return {str(status): int(count) for status, count in rows.all()}

    async def oldest_unpublished_at(self) -> datetime | None:
        table = _table(OutboxEvent)
        statement = select(func.min(table.c.created_at)).where(table.c.published_at.is_(None))
        value = (await self._session.execute(statement)).scalar_one()
        return cast(datetime | None, value)

    async def read_for_update(
        self, investigation_ids: tuple[uuid.UUID, ...]
    ) -> tuple[ObservedInvestigation, ...]:
        if not investigation_ids:
            return ()
        table = _table(Investigation)
        rows = await self._session.execute(
            select(
                table.c.id,
                table.c.status,
                table.c.worker_id,
                table.c.lease_expires_at,
                func.now().label("observed_at"),
            )
            .where(table.c.id.in_(investigation_ids))
            .order_by(table.c.id)
            .with_for_update()
        )
        return tuple(
            ObservedInvestigation(
                id=row.id,
                status=DomainInvestigationStatus(str(row.status)),
                worker_id=row.worker_id,
                lease_expires_at=row.lease_expires_at,
                observed_at=row.observed_at,
            )
            for row in rows.all()
        )

    async def apply_mutation(self, mutation: ReconciliationMutation) -> bool:
        table = _table(Investigation)
        expected_worker = (
            table.c.worker_id.is_(None)
            if mutation.expected_worker_id is None
            else table.c.worker_id == mutation.expected_worker_id
        )
        expected_lease = (
            table.c.lease_expires_at.is_(None)
            if mutation.expected_lease_expires_at is None
            else table.c.lease_expires_at == mutation.expected_lease_expires_at
        )
        result = await self._session.exec(
            update(table)
            .where(
                table.c.id == mutation.investigation_id,
                table.c.status == mutation.expected_status,
                expected_worker,
                expected_lease,
            )
            .values(
                status=mutation.target_status,
                completed_at=mutation.completed_at,
                worker_id=None,
                lease_expires_at=None,
                last_error_code=mutation.error_code,
                last_error_message=mutation.error_message,
            )
            .returning(table.c.id)
        )
        return result.first() is not None

    async def delete_expired(self, *, cutoff: datetime) -> RetentionDeletes:
        outbox = _table(OutboxEvent)
        investigations = _table(Investigation)
        requests = _table(ApiRequest)
        outbox_ids = await self._session.exec(
            delete(outbox)
            .where(
                outbox.c.published_at.is_not(None),
                outbox.c.published_at < cutoff,
            )
            .returning(outbox.c.id)
        )
        investigation_ids = await self._session.exec(
            delete(investigations)
            .where(
                investigations.c.status.in_(
                    (InvestigationStatus.COMPLETED, InvestigationStatus.FAILED)
                ),
                investigations.c.completed_at < cutoff,
            )
            .returning(investigations.c.id)
        )
        referenced_request = (
            select(investigations.c.id)
            .where(investigations.c.request_id == requests.c.id)
            .correlate(requests)
        )
        request_ids = await self._session.exec(
            delete(requests)
            .where(
                requests.c.created_at < cutoff,
                ~exists(referenced_request),
            )
            .returning(requests.c.id)
        )
        return RetentionDeletes(
            outbox_events=len(outbox_ids.all()),
            investigations=len(investigation_ids.all()),
            api_requests=len(request_ids.all()),
        )


def _table(model: type[ApiRequest] | type[Investigation] | type[OutboxEvent]) -> Table:
    return cast(Table, model.__table__)  # type: ignore[union-attr]
