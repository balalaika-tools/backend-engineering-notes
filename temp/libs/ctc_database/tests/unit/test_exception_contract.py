"""Input and failure contracts shared by CTC consumers."""

from unittest.mock import AsyncMock

import pytest
from ctc_database import (
    CtcExceptionRepository,
    ExceptionSourceUnavailableError,
    ExceptionTableNames,
)
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", ['positions"; DROP TABLE exceptions;--', "a.b", ""])
async def test_unsafe_schema_is_rejected_before_connecting(schema: str) -> None:
    engine = AsyncMock(spec=AsyncEngine)
    reader = CtcExceptionRepository(engine, tables=ExceptionTableNames(schema=schema))
    with pytest.raises(ValueError, match="simple SQL identifiers"):
        await reader.fetch("EX-1")
    engine.connect.assert_not_called()


@pytest.mark.asyncio
async def test_connection_failure_has_stable_public_error() -> None:
    engine = AsyncMock(spec=AsyncEngine)
    engine.connect.side_effect = OSError("connection refused")
    reader = CtcExceptionRepository(engine, tables=ExceptionTableNames(schema="positions"))
    with pytest.raises(ExceptionSourceUnavailableError) as raised:
        await reader.fetch("EX-1")
    assert raised.value.error_code == "ctc_database_unavailable"
