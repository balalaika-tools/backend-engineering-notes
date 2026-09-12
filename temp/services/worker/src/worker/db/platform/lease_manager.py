"""Committed lease operations used by process lifecycle tasks."""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from worker.db.platform.repositories import InvestigationRepository
from worker.domain.investigation import InvestigationState


class SqlAlchemyLeaseManager:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, investigation_id: uuid.UUID) -> InvestigationState | None:
        async with self._session_factory() as session:
            return await InvestigationRepository(session).get(investigation_id)

    async def claim(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> InvestigationState | None:
        async with self._session_factory() as session:
            state = await InvestigationRepository(session).claim(
                investigation_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            await session.commit()
        return state

    async def extend_lease(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> bool:
        async with self._session_factory() as session:
            updated = await InvestigationRepository(session).extend_lease(
                investigation_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            await session.commit()
        return updated
