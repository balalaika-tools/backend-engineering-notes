"""SQLAlchemy transaction boundary for one investigation execution."""

from types import TracebackType
from typing import Self

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from worker.db.platform.repositories import InvestigationRepository
from worker.ports.investigation.investigation_store import InvestigationStoreUnavailableError


class SqlAlchemyInvestigationUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session = session_factory()
        self.investigations = InvestigationRepository(self._session)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                try:
                    await self._session.rollback()
                finally:
                    await self._session.close()
            else:
                await self._session.close()
        except (OSError, SQLAlchemyError) as exc:
            raise InvestigationStoreUnavailableError(str(exc)) from exc
        if exc_value is not None and isinstance(exc_value, (OSError, SQLAlchemyError)):
            raise InvestigationStoreUnavailableError(str(exc_value)) from exc_value

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except (OSError, SQLAlchemyError) as exc:
            raise InvestigationStoreUnavailableError(str(exc)) from exc
