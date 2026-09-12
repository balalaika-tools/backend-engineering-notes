"""SQLAlchemy transaction boundary for accepting investigation batches."""

from types import TracebackType
from typing import Self

from orchestrator.db.repositories.config_state import ConfigStateRepository
from orchestrator.db.repositories.investigations import InvestigationRepository
from orchestrator.db.repositories.outbox import OutboxRepository
from orchestrator.db.repositories.requests import RequestRepository
from orchestrator.ports.config_store import ConfigStoreUnavailableError
from orchestrator.ports.investigation_store import InvestigationStoreUnavailableError
from orchestrator.ports.outbox_store import OutboxStoreUnavailableError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession


class SqlAlchemyInvestigationUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session = session_factory()
        self.requests = RequestRepository(self._session)
        self.investigations = InvestigationRepository(self._session)
        self.outbox = OutboxRepository(self._session)

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


class SqlAlchemyConfigUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session = session_factory()
        self.config_state = ConfigStateRepository(self._session)

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
            raise ConfigStoreUnavailableError(str(exc)) from exc
        if exc_value is not None and isinstance(exc_value, (OSError, SQLAlchemyError)):
            raise ConfigStoreUnavailableError(str(exc_value)) from exc_value

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except (OSError, SQLAlchemyError) as exc:
            raise ConfigStoreUnavailableError(str(exc)) from exc


class SqlAlchemyOutboxUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session = session_factory()
        self.outbox = OutboxRepository(self._session)

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
            raise OutboxStoreUnavailableError(str(exc)) from exc
        if exc_value is not None and isinstance(exc_value, (OSError, SQLAlchemyError)):
            raise OutboxStoreUnavailableError(str(exc_value)) from exc_value

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except (OSError, SQLAlchemyError) as exc:
            raise OutboxStoreUnavailableError(str(exc)) from exc
