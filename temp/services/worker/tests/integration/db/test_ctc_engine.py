"""CTC connection safeguards against disposable PostgreSQL."""

import os

import pytest
from sqlalchemy import make_url
from worker.db.ctc.engine import build_engine
from worker.db.ctc.query_executor import CtcQueryExecutor
from worker.ports.investigation.query_executor import QueryUnavailableError

pytestmark = [pytest.mark.integration, pytest.mark.requires_env("INTEGRATION_CTC_DATABASE_URL")]


def _database_url() -> str:
    value = os.environ.get("INTEGRATION_CTC_DATABASE_URL")
    if not value:
        pytest.skip("INTEGRATION_CTC_DATABASE_URL is required")
    return value


@pytest.mark.asyncio
async def test_agent_queries_use_transaction_local_safety_settings() -> None:
    url = make_url(_database_url())
    engine = build_engine(
        database_url=url,
        pool_size=2,
    )
    executor = CtcQueryExecutor(
        engine,
        row_limit=10,
        statement_timeout_seconds=0.05,
        work_mem_bytes=64 * 1024 * 1024,
    )
    settings = await executor.execute(
        "SELECT current_setting('transaction_read_only') AS read_only, "
        "current_setting('work_mem') AS work_mem"
    )
    assert settings.rows == ({"read_only": "on", "work_mem": "64MB"},)

    with pytest.raises(QueryUnavailableError, match="statement timeout"):
        await executor.execute("SELECT pg_sleep(0.2)")
    await engine.dispose()
