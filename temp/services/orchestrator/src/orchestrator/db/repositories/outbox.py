"""Persistence operations for transactional outbox events."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from orchestrator.domain.investigation import InvestigationRecord
from orchestrator.domain.outbox import OutboxRecord
from platform_db import OutboxEvent
from sqlalchemy import Table, func, update
from sqlalchemy import select as sa_select
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_investigation_requested(
        self,
        *,
        investigation: InvestigationRecord,
        traceparent: str,
        subject: str,
    ) -> uuid.UUID:
        event = OutboxEvent(
            event_id=investigation.event_id,
            aggregate_type="investigation",
            aggregate_id=investigation.id,
            event_type="investigation.requested",
            subject=subject,
            payload={
                "event_id": str(investigation.event_id),
                "event_type": "investigation.requested",
                "occurred_at": datetime.now(UTC).isoformat(),
                "investigation_id": str(investigation.id),
                "request_id": str(investigation.request_id),
                "client_id": investigation.client_id,
                "exception_id": investigation.exception_id,
                "traceparent": traceparent,
            },
        )
        self._session.add(event)
        await self._session.flush()
        return event.event_id

    async def claim_pending(self, *, limit: int) -> list[OutboxRecord]:
        table = cast(Table, OutboxEvent.__table__)  # type: ignore[attr-defined]
        statement = (
            select(OutboxEvent)
            .where(
                table.c.published_at.is_(None),
                table.c.next_attempt_at <= func.now(),
            )
            .order_by(table.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self._session.exec(statement)).all()
        return [_record(row) for row in rows]

    async def count_pending(self) -> int:
        table = cast(Table, OutboxEvent.__table__)  # type: ignore[attr-defined]
        statement = sa_select(func.count()).select_from(table).where(table.c.published_at.is_(None))
        return int((await self._session.execute(statement)).scalar_one())

    async def mark_published(self, outbox_id: int) -> None:
        table = cast(Table, OutboxEvent.__table__)  # type: ignore[attr-defined]
        statement = (
            update(table)
            .where(table.c.id == outbox_id, table.c.published_at.is_(None))
            .values(published_at=func.now(), last_error=None)
        )
        await self._session.exec(statement)

    async def mark_failed(
        self,
        outbox_id: int,
        *,
        error: str,
        retry_after_seconds: float,
    ) -> None:
        table = cast(Table, OutboxEvent.__table__)  # type: ignore[attr-defined]
        statement = (
            update(table)
            .where(table.c.id == outbox_id, table.c.published_at.is_(None))
            .values(
                attempt_count=table.c.attempt_count + 1,
                last_error=error[:2000],
                next_attempt_at=func.now() + timedelta(seconds=retry_after_seconds),
            )
        )
        await self._session.exec(statement)


def _record(model: OutboxEvent) -> OutboxRecord:
    if model.id is None:
        raise RuntimeError("Persisted outbox event has no row identifier")
    return OutboxRecord(
        id=model.id,
        event_id=model.event_id,
        subject=model.subject,
        payload=model.payload,
        attempt_count=model.attempt_count,
    )
