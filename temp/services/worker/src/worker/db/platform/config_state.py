"""Platform configuration-generation reads."""

from platform_db import ConfigState
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from worker.ports.control_context.config_generation import ConfigGenerationUnavailableError


class _ConfigStateMissingError(RuntimeError):
    pass


class ConfigStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_generation(self) -> int:
        state = await self._session.get(ConfigState, 1)
        if state is None:
            raise _ConfigStateMissingError("The config_state singleton row is missing")
        return state.generation


class SqlAlchemyConfigGenerationSource:
    """Read the generation in a short session owned by each resolution call."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_generation(self) -> int:
        try:
            async with self._session_factory() as session:
                return await ConfigStateRepository(session).get_generation()
        except (SQLAlchemyError, _ConfigStateMissingError) as exc:
            raise ConfigGenerationUnavailableError(
                "The authoritative configuration generation is unavailable"
            ) from exc
