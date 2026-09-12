"""Selection policy and validation independent of databases or HTTP."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from orchestrator.application.request_filtered_investigations import (
    BulkLimitExceededError,
    InvalidFilteredSelectionError,
    RequestFilteredInvestigations,
)
from orchestrator.ports.exception_candidates import CandidateCursor, CandidateSourceUnavailableError


def _action(pages: list[tuple[CandidateCursor, ...]], completed: set[str] | None = None):
    source = AsyncMock()
    source.read_page.side_effect = pages
    uow = AsyncMock()
    uow.__aenter__.return_value = uow
    uow.investigations.completed_exception_ids.return_value = completed or set()
    uow.investigations.insert_or_attach_filtered.return_value = None
    uow.requests.add.return_value = uuid.uuid4()
    action = RequestFilteredInvestigations(
        candidates=source,
        unit_of_work_factory=lambda: uow,
        schema="positions",
        exception_name="Break",
        max_exceptions=3,
        page_size=2,
        subject="investigations.requested",
    )
    return action, source, uow


def _row(name: str, pk: int) -> CandidateCursor:
    return CandidateCursor(datetime(2026, 9, 11, tzinfo=UTC), name, bytes([pk]))


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum", [0, -1, True, 1.5, 4])
async def test_invalid_volume_fails_before_any_port_call(maximum) -> None:
    action, source, uow = _action([])
    with pytest.raises((BulkLimitExceededError, InvalidFilteredSelectionError)):
        await action.execute(client_id="client", traceparent="", max_exceptions=maximum)
    source.read_page.assert_not_called()
    uow.__aenter__.assert_not_called()


@pytest.mark.asyncio
async def test_naive_date_fails_before_any_port_call() -> None:
    action, source, uow = _action([])
    with pytest.raises(InvalidFilteredSelectionError):
        await action.execute(
            client_id="client", traceparent="", created_at_gte=datetime(2026, 9, 11)
        )
    source.read_page.assert_not_called()
    uow.__aenter__.assert_not_called()


@pytest.mark.asyncio
async def test_scans_past_completed_history_and_deduplicates_ids_across_pages() -> None:
    first = (_row("A", 1), _row("B", 2))
    second = (_row("B", 3), _row("C", 4))
    action, source, uow = _action([first, second], {"A"})
    await action.execute(client_id="client", traceparent="", max_exceptions=2)
    assert source.read_page.await_count == 2
    assert source.read_page.await_args_list[1].kwargs["after"] == first[-1]
    assert [
        call.kwargs["exception_id"]
        for call in uow.investigations.insert_or_attach_filtered.await_args_list
    ] == ["B", "C"]


@pytest.mark.asyncio
async def test_default_stops_after_first_eligible_candidate_and_normalizes_offset() -> None:
    action, source, uow = _action([(_row("A", 1), _row("B", 2))])
    await action.execute(
        client_id="client",
        traceparent="",
        created_at_gte=datetime.fromisoformat("2026-09-11T03:00:00+03:00"),
    )
    assert source.read_page.await_count == 1
    selection = source.read_page.await_args.kwargs["selection"]
    assert selection.created_at_gte == datetime(2026, 9, 11, tzinfo=UTC)
    assert selection.created_at_gte.tzinfo is UTC
    assert uow.investigations.insert_or_attach_filtered.await_count == 1


@pytest.mark.asyncio
async def test_exhaustion_creates_durable_empty_request() -> None:
    action, source, uow = _action([()])
    result = await action.execute(client_id="client", traceparent="")
    assert result.investigations == ()
    uow.commit.assert_awaited_once()
    assert uow.requests.add.await_args.kwargs["selection"].max_exceptions == 1
    assert uow.requests.set_selected_ids.await_args.kwargs["exception_ids"] == []


@pytest.mark.asyncio
async def test_repeated_cursor_fails_without_creating_request() -> None:
    page = (_row("A", 1), _row("B", 2))
    action, source, uow = _action([page, page], {"A", "B"})
    with pytest.raises(CandidateSourceUnavailableError):
        await action.execute(client_id="client", traceparent="")
    assert source.read_page.await_count == 2
    uow.requests.add.assert_not_called()
