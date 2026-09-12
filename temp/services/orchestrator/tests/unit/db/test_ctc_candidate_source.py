"""Shared-reader cursor and error translation into the caller-owned port."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import ctc_database
import pytest
from orchestrator.db.ctc_candidate_source import CtcCandidateSource
from orchestrator.ports.exception_candidates import (
    CandidateCursor,
    CandidateSourceUnavailableError,
    FilteredSelection,
)


@pytest.mark.asyncio
async def test_cursor_and_results_are_translated() -> None:
    reader = AsyncMock(spec=ctc_database.CtcCandidateReader)
    timestamp = datetime(2026, 9, 11, tzinfo=UTC)
    reader.read_page.return_value = (ctc_database.CandidateCursor(timestamp, "B", b"2"),)
    result = await CtcCandidateSource(reader).read_page(
        selection=FilteredSelection(timestamp, 3, "positions", "Break"),
        limit=2,
        after=CandidateCursor(timestamp, "A", b"1"),
    )
    assert result == (CandidateCursor(timestamp, "B", b"2"),)
    assert reader.read_page.await_args.kwargs == dict(
        schema="positions",
        exception_name="Break",
        created_at_gte=timestamp,
        limit=2,
        after=ctc_database.CandidateCursor(timestamp, "A", b"1"),
    )


@pytest.mark.asyncio
async def test_database_failure_becomes_caller_owned_unavailability() -> None:
    reader = AsyncMock(spec=ctc_database.CtcCandidateReader)
    reader.read_page.side_effect = ctc_database.ExceptionSourceUnavailableError("query failed")
    with pytest.raises(CandidateSourceUnavailableError) as raised:
        await CtcCandidateSource(reader).read_page(
            selection=FilteredSelection(None, 1, "positions", "Break"), limit=2, after=None
        )
    assert raised.value.error_code == "ctc_database_unavailable"
