"""Conditional investigation lifecycle updates for worker ownership."""

import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import cast

from platform_db import Investigation
from platform_db import InvestigationStatus as DatabaseStatus
from sqlalchemy import Table, and_, func, or_, update
from sqlalchemy.sql.dml import Update
from sqlmodel.ext.asyncio.session import AsyncSession
from worker.domain.investigation import InvestigationState, InvestigationStatus
from worker.ports.investigation.investigation_store import (
    InvestigationFailureMutation,
    WriteBackStep,
)


class InvestigationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]

    async def get(self, investigation_id: uuid.UUID) -> InvestigationState | None:
        # Core UPDATE statements bypass ORM identity synchronization; always refresh
        # so takeover and checkpoint decisions observe the committed database owner.
        model = await self._session.get(
            Investigation,
            investigation_id,
            populate_existing=True,
        )
        return _state(model) if model else None

    async def claim(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> InvestigationState | None:
        lease = timedelta(seconds=lease_seconds)
        claimable = or_(
            self._table.c.status == DatabaseStatus.QUEUED,
            and_(
                self._table.c.status == DatabaseStatus.PROCESSING,
                self._table.c.lease_expires_at < func.now(),
            ),
        )
        claimed_id = await self._returning_id(
            update(self._table)
            .where(self._table.c.id == investigation_id, claimable)
            .values(
                status=DatabaseStatus.PROCESSING,
                worker_id=worker_id,
                lease_expires_at=func.now() + lease,
                last_heartbeat_at=func.now(),
                started_at=func.coalesce(self._table.c.started_at, func.now()),
                attempt_count=self._table.c.attempt_count + 1,
            )
        )
        return await self.get(claimed_id) if claimed_id else None

    async def extend_lease(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> bool:
        return await self._owned_update(
            investigation_id,
            worker_id=worker_id,
            values={
                "lease_expires_at": func.now() + timedelta(seconds=lease_seconds),
                "last_heartbeat_at": func.now(),
            },
        )

    async def apply_failure_transition(self, mutation: InvestigationFailureMutation) -> bool:
        return await self._owned_update(
            mutation.investigation_id,
            worker_id=mutation.worker_id,
            expected_attempt_count=mutation.expected_attempt_count,
            values={
                "status": DatabaseStatus(mutation.target_status.value),
                "completed_at": mutation.completed_at,
                "worker_id": None,
                "lease_expires_at": None,
                "last_error_code": mutation.error_code,
                "last_error_message": mutation.error_message[:4000],
            },
        )

    async def persist_analysis(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        analysis: Mapping[str, object],
        report: str | None,
        report_uri: str,
    ) -> bool:
        return await self._owned_update(
            investigation_id,
            worker_id=worker_id,
            values={
                "analysis": dict(analysis),
                "analysis_persisted_at": func.now(),
                "report": report,
                "report_uri": report_uri,
            },
        )

    async def record_write_back_step(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        step: WriteBackStep,
        outcome: str,
        detail: str | None = None,
    ) -> bool:
        values: dict[str, object] = {f"{step}_outcome": outcome}
        if outcome == "written":
            values[f"{step}_written_at"] = func.now()
        values[f"{step}_detail"] = detail
        return await self._owned_update(
            investigation_id,
            worker_id=worker_id,
            values=values,
        )

    async def complete(self, investigation_id: uuid.UUID, *, worker_id: str) -> bool:
        return await self._owned_update(
            investigation_id,
            worker_id=worker_id,
            values={
                "status": DatabaseStatus.COMPLETED,
                "completed_at": func.now(),
                "worker_id": None,
                "lease_expires_at": None,
            },
        )

    async def _owned_update(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        expected_attempt_count: int | None = None,
        values: Mapping[str, object],
    ) -> bool:
        conditions = [
            self._table.c.id == investigation_id,
            self._table.c.status == DatabaseStatus.PROCESSING,
            self._table.c.worker_id == worker_id,
        ]
        if expected_attempt_count is not None:
            conditions.append(self._table.c.attempt_count == expected_attempt_count)
        statement = update(self._table).where(*conditions).values(**values)
        result = await self._session.exec(statement)
        return result.rowcount == 1

    async def _returning_id(self, statement: Update) -> uuid.UUID | None:
        executable = statement.returning(self._table.c.id)
        value = (await self._session.exec(executable)).scalar_one_or_none()
        return cast(uuid.UUID | None, value)


def _state(model: Investigation) -> InvestigationState:
    return InvestigationState(
        id=model.id,
        request_id=model.request_id,
        exception_id=model.exception_id,
        status=InvestigationStatus(model.status.value),
        attempt_count=model.attempt_count,
        worker_id=model.worker_id,
        lease_expires_at=model.lease_expires_at,
        last_heartbeat_at=model.last_heartbeat_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
        last_error_code=model.last_error_code,
        analysis=model.analysis,
        analysis_persisted_at=model.analysis_persisted_at,
        report=model.report,
        report_uri=model.report_uri,
        comment_outcome=model.comment_outcome,
        comment_detail=model.comment_detail,
        codes_outcome=model.codes_outcome,
        codes_detail=model.codes_detail,
    )
