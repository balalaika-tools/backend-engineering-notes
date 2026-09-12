"""Stable failure translation for authoritative configuration-generation reads."""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from worker.db.platform.config_state import SqlAlchemyConfigGenerationSource
from worker.ports.control_context.config_generation import ConfigGenerationUnavailableError


class _SessionContext:
    def __init__(self, session: AsyncSession, cleanup_error: BaseException | None) -> None:
        self._session = session
        self._cleanup_error = cleanup_error

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_args: object) -> None:
        if self._cleanup_error is not None:
            raise self._cleanup_error


def _source(
    *,
    state: object | None = SimpleNamespace(generation=7),
    query_error: BaseException | None = None,
    cleanup_error: BaseException | None = None,
) -> SqlAlchemyConfigGenerationSource:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = state
    if query_error is not None:
        session.get.side_effect = query_error

    context = _SessionContext(cast(AsyncSession, session), cleanup_error)
    factory = cast(async_sessionmaker[AsyncSession], MagicMock(return_value=context))
    return SqlAlchemyConfigGenerationSource(factory)


@pytest.mark.asyncio
async def test_generation_read_returns_authoritative_value() -> None:
    assert await _source().get_generation() == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["query", "cleanup"])
async def test_database_failures_are_translated_at_the_port_boundary(
    failure_stage: str,
) -> None:
    error = OperationalError("select generation", {}, RuntimeError("database unavailable"))
    source = _source(
        query_error=error if failure_stage == "query" else None,
        cleanup_error=error if failure_stage == "cleanup" else None,
    )

    with pytest.raises(ConfigGenerationUnavailableError) as raised:
        await source.get_generation()

    assert raised.value.error_code == "platform_database_unavailable"
    assert raised.value.__cause__ is error


@pytest.mark.asyncio
async def test_session_connection_failure_is_translated_at_the_port_boundary() -> None:
    error = OperationalError("connect", {}, RuntimeError("database unavailable"))
    factory = cast(async_sessionmaker[AsyncSession], MagicMock(side_effect=error))

    with pytest.raises(ConfigGenerationUnavailableError) as raised:
        await SqlAlchemyConfigGenerationSource(factory).get_generation()

    assert raised.value.__cause__ is error


@pytest.mark.asyncio
async def test_missing_singleton_is_reported_as_generation_unavailable() -> None:
    with pytest.raises(ConfigGenerationUnavailableError) as raised:
        await _source(state=None).get_generation()

    assert raised.value.error_code == "platform_database_unavailable"


@pytest.mark.asyncio
async def test_cancellation_is_not_translated() -> None:
    source = _source(query_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await source.get_generation()
