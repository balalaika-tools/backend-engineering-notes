"""Acceptance of the repository's synthetic CTC initialization and reader grants."""

import os
from datetime import UTC, datetime

import pytest
from ctc_database import CtcCandidateReader, CtcExceptionRepository, build_engine
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError

pytestmark = [pytest.mark.integration, pytest.mark.requires_env("INTEGRATION_CTC_FIXTURE_URL")]


@pytest.mark.asyncio
async def test_seeded_selection_and_existing_worker_fetch_use_read_only_role() -> None:
    engine = build_engine(
        database_url=make_url(os.environ["INTEGRATION_CTC_FIXTURE_URL"]),
        pool_size=1,
    )
    try:
        reader = CtcCandidateReader(engine, statement_timeout_seconds=1)
        rows = await reader.read_page(schema="webuiPositions", exception_name="Break", limit=20)
        assert [row.external_id for row in rows] == [
            "EX-PRE-BOUND",
            "EX-1",
            "EX-ELIGIBLE-A",
            "EX-ELIGIBLE-B",
        ]
        selected = []
        cursor = None
        for _ in range(3):
            page = await reader.read_page(
                schema="webuiPositions",
                exception_name="Break",
                limit=1,
                created_at_gte=datetime(2026, 9, 11, tzinfo=UTC),
                after=cursor,
            )
            if not page:
                break
            selected.extend(row.external_id for row in page)
            cursor = page[-1]
        assert selected == ["EX-ELIGIBLE-A", "EX-ELIGIBLE-B"]
        original = await CtcExceptionRepository(engine).fetch("EX-1", rec_schema="webuiPositions")
        assert original.exception_pk == b"\x01"
        assert len(original.records) == 2
        async with engine.begin() as connection:
            with pytest.raises(DBAPIError, match="permission denied"):
                await connection.execute(
                    text('DELETE FROM "webuiPositions".exceptions WHERE false')
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_read_only_acceptance_outputs_only_aggregate_status_signals() -> None:
    from dataclasses import asdict

    from ctc_database import CtcAcceptanceError, verify_candidate_contract

    engine = build_engine(
        database_url=make_url(os.environ["INTEGRATION_CTC_FIXTURE_URL"]), pool_size=1
    )
    try:
        report = await verify_candidate_contract(
            engine, schema="webuiPositions", exception_name="Break", statement_timeout_seconds=2
        )
        rendered = str(asdict(report))
        assert report.eligible_sample_count == 4
        assert {row["status"] for row in report.status_relationships} == {1, 2, 3}
        assert "EX-" not in rendered
        assert "Synthetic" not in rendered
        assert "local-ctc-password" not in rendered
        with pytest.raises(CtcAcceptanceError, match="configured_exception_name"):
            await verify_candidate_contract(
                engine,
                schema="webuiPositions",
                exception_name="Missing",
                statement_timeout_seconds=2,
            )
    finally:
        await engine.dispose()
