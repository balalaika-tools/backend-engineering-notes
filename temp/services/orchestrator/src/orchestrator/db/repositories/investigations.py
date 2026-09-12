"""Persistence operations for investigation lifecycle records."""

import uuid
from typing import cast

from orchestrator.domain.investigation import (
    InvestigationInsertResult,
    InvestigationRecord,
    InvestigationReportRecord,
)
from orchestrator.domain.investigation import (
    InvestigationStatus as DomainStatus,
)
from platform_db import (
    ACTIVE_STATUS_PREDICATE,
    ApiRequestInvestigation,
    Investigation,
    InvestigationStatus,
)
from sqlalchemy import Table, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


class InvestigationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_or_attach(
        self,
        *,
        request_id: uuid.UUID,
        client_id: str,
        exception_id: str,
        investigation_id: uuid.UUID | None = None,
        event_id: uuid.UUID | None = None,
    ) -> InvestigationInsertResult:
        table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
        values = {
            "id": investigation_id or uuid.uuid4(),
            "request_id": request_id,
            "client_id": client_id,
            "exception_id": exception_id,
            "event_id": event_id or uuid.uuid4(),
            "status": InvestigationStatus.QUEUED,
        }
        statement = (
            insert(table)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["exception_id"],
                index_where=ACTIVE_STATUS_PREDICATE,
            )
            .returning(*table.c)
        )
        for _attempt in range(2):
            created = (await self._session.execute(statement)).mappings().one_or_none()
            if created is not None:
                return InvestigationInsertResult(
                    investigation=_record_from_mapping(created),
                    created=True,
                )
            active = await self._active_for_exception(exception_id)
            if active is not None:
                return InvestigationInsertResult(investigation=_record(active), created=False)
        raise RuntimeError("Active-investigation conflict did not yield an attachable row")

    async def completed_exception_ids(self, exception_ids: list[str]) -> set[str]:
        if not exception_ids:
            return set()
        table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
        statement = (
            select(table.c.exception_id)
            .where(
                table.c.exception_id.in_(exception_ids),
                table.c.status == InvestigationStatus.COMPLETED,
            )
            .distinct()
        )
        return set((await self._session.execute(statement)).scalars().all())

    async def insert_or_attach_filtered(
        self, *, request_id: uuid.UUID, client_id: str, exception_id: str
    ) -> InvestigationInsertResult | None:
        if await self.completed_exception_ids([exception_id]):
            return None
        result = await self.insert_or_attach(
            request_id=request_id, client_id=client_id, exception_id=exception_id
        )
        # A terminal transition can release the active unique key after the first read.
        # READ COMMITTED gives this statement a snapshot after the insert/conflict wait.
        if await self.completed_exception_ids([exception_id]):
            if result.created:
                table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
                await self._session.execute(
                    delete(table).where(table.c.id == result.investigation.id)
                )
            return None
        return result

    async def get(self, investigation_id: uuid.UUID) -> InvestigationRecord | None:
        investigation = await self._session.get(Investigation, investigation_id)
        return _record(investigation) if investigation else None

    async def by_ids(
        self, investigation_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, InvestigationRecord]:
        if not investigation_ids:
            return {}
        table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
        statement = select(Investigation).where(table.c.id.in_(investigation_ids))
        rows = (await self._session.exec(statement)).all()
        return {row.id: _record(row) for row in rows}

    async def by_api_request_id(self, request_id: uuid.UUID) -> list[InvestigationRecord]:
        investigation_table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
        membership_table = cast(
            Table,
            ApiRequestInvestigation.__table__,  # type: ignore[attr-defined]
        )
        statement = (
            select(Investigation)
            .join(
                membership_table,
                membership_table.c.investigation_id == investigation_table.c.id,
            )
            .where(membership_table.c.request_id == request_id)
            .order_by(membership_table.c.position)
        )
        rows = (await self._session.exec(statement)).all()
        return [_record(row) for row in rows]

    async def most_recent_by_exception_ids(
        self, exception_ids: list[str]
    ) -> dict[str, InvestigationRecord]:
        if not exception_ids:
            return {}
        table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
        statement = (
            select(Investigation)
            .where(table.c.exception_id.in_(exception_ids))
            .distinct(table.c.exception_id)
            .order_by(table.c.exception_id, table.c.created_at.desc())
        )
        rows = (await self._session.exec(statement)).all()
        return {row.exception_id: _record(row) for row in rows}

    async def reports_by_ids(
        self, investigation_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, InvestigationReportRecord]:
        if not investigation_ids:
            return {}
        table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
        statement = select(Investigation).where(table.c.id.in_(investigation_ids))
        rows = (await self._session.exec(statement)).all()
        return {row.id: _report_record(row) for row in rows}

    async def most_recent_reports_by_exception_ids(
        self, exception_ids: list[str]
    ) -> dict[str, InvestigationReportRecord]:
        if not exception_ids:
            return {}
        table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
        statement = (
            select(Investigation)
            .where(table.c.exception_id.in_(exception_ids))
            .distinct(table.c.exception_id)
            .order_by(table.c.exception_id, table.c.created_at.desc(), table.c.id.desc())
        )
        rows = (await self._session.exec(statement)).all()
        return {row.exception_id: _report_record(row) for row in rows}

    async def _active_for_exception(self, exception_id: str) -> Investigation | None:
        table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
        statement = (
            select(Investigation)
            .where(
                table.c.exception_id == exception_id,
                table.c.status.in_([InvestigationStatus.QUEUED, InvestigationStatus.PROCESSING]),
            )
            .order_by(table.c.created_at.desc())
            .limit(1)
        )
        return (await self._session.exec(statement)).first()


def _record(model: Investigation) -> InvestigationRecord:
    return InvestigationRecord(
        id=model.id,
        request_id=model.request_id,
        client_id=model.client_id,
        exception_id=model.exception_id,
        event_id=model.event_id,
        status=DomainStatus(model.status.value),
        attempt_count=model.attempt_count,
        created_at=model.created_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
        last_error_code=model.last_error_code,
    )


def _record_from_mapping(row: RowMapping) -> InvestigationRecord:
    status = row["status"]
    return InvestigationRecord(
        id=cast(uuid.UUID, row["id"]),
        request_id=cast(uuid.UUID, row["request_id"]),
        client_id=str(row["client_id"]),
        exception_id=str(row["exception_id"]),
        event_id=cast(uuid.UUID, row["event_id"]),
        status=DomainStatus(
            status.value if isinstance(status, InvestigationStatus) else str(status)
        ),
        attempt_count=int(row["attempt_count"]),
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        last_error_code=row["last_error_code"],
    )


def _report_record(model: Investigation) -> InvestigationReportRecord:
    return InvestigationReportRecord(
        id=model.id,
        exception_id=model.exception_id,
        status=DomainStatus(model.status.value),
        completed_at=model.completed_at,
        last_error_code=model.last_error_code,
        analysis=model.analysis,
        report=model.report,
        report_uri=model.report_uri,
        comment_outcome=model.comment_outcome,
        comment_detail=model.comment_detail,
        codes_outcome=model.codes_outcome,
        codes_detail=model.codes_detail,
    )
