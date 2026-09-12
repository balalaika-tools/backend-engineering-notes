"""Probe output cannot reveal database credentials or customer rows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import ctc_database
import pytest
from orchestrator.bootstrap import ctc_acceptance_probe as probe
from pydantic import SecretStr


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        None,
        ctc_database.CtcAcceptanceError("required_columns"),
        ctc_database.ExceptionSourceUnavailableError(
            "password=credential-canary row-payload-canary"
        ),
    ],
)
async def test_probe_redacts_failures_and_disposes_pool(monkeypatch, capsys, error) -> None:
    engine = SimpleNamespace(dispose=AsyncMock())
    settings = SimpleNamespace(
        ctc_database_url="postgresql+asyncpg://reader@localhost/ctc",
        ctc_acquisition_timeout_seconds=2,
        ctc_statement_timeout_seconds=2,
        ctc_reconciliation_schema="positions",
        exception_name="Break",
    )
    monkeypatch.setattr(probe, "get_settings", lambda: settings)
    monkeypatch.setattr(
        probe,
        "get_secrets",
        lambda: SimpleNamespace(ctc_db_password=SecretStr("credential-canary")),
    )
    monkeypatch.setattr(probe.ctc_database, "build_engine", lambda **kwargs: engine)
    verify = AsyncMock(
        return_value=ctc_database.CtcAcceptanceReport(
            "positions",
            "Break",
            0,
            ({"status": 1, "resolved": False, "closed": False, "count": 3},),
        )
    )
    if error:
        verify.side_effect = error
    monkeypatch.setattr(probe.ctc_database, "verify_candidate_contract", verify)
    assert await probe.run() == (1 if error else 0)
    output = capsys.readouterr().out
    assert "credential-canary" not in output
    assert "row-payload-canary" not in output
    assert '"accepted": false' in output if error else '"accepted": true' in output
    engine.dispose.assert_awaited_once()
