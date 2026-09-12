"""Worker-owned failure translation at the shared-reader boundary."""

from unittest.mock import AsyncMock

import ctc_database
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from worker.db.ctc.exception_repository import CtcExceptionRepository, ExceptionTableNames
from worker.ports.investigation.exception_source import ExceptionSourceUnavailableError


@pytest.mark.asyncio
async def test_shared_database_failure_becomes_worker_port_error() -> None:
    engine = AsyncMock(spec=AsyncEngine)
    engine.connect.side_effect = OSError("unavailable")
    repository = CtcExceptionRepository(engine, tables=ExceptionTableNames(schema="positions"))
    with pytest.raises(ExceptionSourceUnavailableError) as raised:
        await repository.fetch("EX-1")
    assert isinstance(raised.value.__cause__, ctc_database.ExceptionSourceUnavailableError)
