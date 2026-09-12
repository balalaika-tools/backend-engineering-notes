"""Durability, history eligibility, and admission races on real PostgreSQL."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from orchestrator.application.query_status import QueryInvestigationRequestStatus
from orchestrator.application.request_filtered_investigations import RequestFilteredInvestigations
from orchestrator.application.request_investigations import RequestInvestigations
from orchestrator.db.repositories.investigations import InvestigationRepository
from orchestrator.db.session import build_session_factory
from orchestrator.db.unit_of_work import SqlAlchemyInvestigationUnitOfWork
from orchestrator.ports.exception_candidates import CandidateCursor, CandidateSourceUnavailableError
from platform_db import ApiRequest, ApiRequestInvestigation, Investigation, OutboxEvent
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_env("INTEGRATION_PLATFORM_DATABASE_URL"),
]


class Candidates:
    def __init__(self, ids: list[str]) -> None:
        self.rows = tuple(
            CandidateCursor(datetime(2026, 9, 11, tzinfo=UTC), key, bytes([i]))
            for i, key in enumerate(sorted(ids))
        )

    async def read_page(self, *, selection, limit, after):
        rows = self.rows
        if after:
            rows = tuple(
                row
                for row in rows
                if (row.created_at, row.external_id, row.pk)
                > (after.created_at, after.external_id, after.pk)
            )
        return rows[:limit]


@pytest_asyncio.fixture
async def factory() -> AsyncIterator:
    engine = create_async_engine(os.environ["INTEGRATION_PLATFORM_DATABASE_URL"])
    yield build_session_factory(engine)
    await engine.dispose()


def _filtered(factory, ids, source=None):
    return RequestFilteredInvestigations(
        candidates=source or Candidates(ids),
        unit_of_work_factory=lambda: SqlAlchemyInvestigationUnitOfWork(factory),
        schema="positions",
        exception_name="Break",
        max_exceptions=10,
        page_size=2,
        subject="investigations.requested",
    )


async def _explicit(factory, ids):
    return await RequestInvestigations(
        unit_of_work_factory=lambda: SqlAlchemyInvestigationUnitOfWork(factory),
        max_batch_size=10,
        subject="investigations.requested",
    ).execute(client_id="seed", exception_ids=ids, traceparent="")


@pytest.mark.asyncio
async def test_history_filter_failed_retry_active_attachment_and_audit(factory) -> None:
    prefix = uuid.uuid4().hex
    completed, failed, active, missing = [f"{prefix}-{suffix}" for suffix in "abcd"]
    old = await _explicit(factory, [completed, failed, active])
    async with factory() as session:
        await session.execute(
            update(Investigation)
            .where(Investigation.exception_id == completed)
            .values(status="completed")
        )
        await session.execute(
            update(Investigation)
            .where(Investigation.exception_id == failed)
            .values(status="failed")
        )
        await session.commit()
    # Explicit reruns remain allowed; any retained completion still excludes filtered selection.
    rerun = await _explicit(factory, [completed])
    async with factory() as session:
        await session.execute(
            update(Investigation)
            .where(Investigation.id == rerun.investigations[0].investigation_id)
            .values(status="failed")
        )
        await session.commit()
    result = await _filtered(factory, [completed, failed, active, missing]).execute(
        client_id="client",
        traceparent="",
        max_exceptions=4,
        created_at_gte=datetime(2026, 9, 11, tzinfo=UTC),
    )
    assert [row.exception_id for row in result.investigations] == [failed, active, missing]
    assert [row.created for row in result.investigations] == [True, False, True]
    assert result.investigations[1].investigation_id == old.investigations[2].investigation_id
    async with factory() as session:
        request = await session.get(ApiRequest, result.request_id)
        assert request.request_kind == "filtered"
        assert request.exception_ids == [failed, active, missing]
        assert request.selection_max_exceptions == 4
        assert request.selection_schema == "positions"
        assert request.selection_exception_name == "Break"
        assert request.selection_created_at_gte == datetime(2026, 9, 11, tzinfo=UTC)
        events = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .join(Investigation, OutboxEvent.aggregate_id == Investigation.id)
                .where(Investigation.request_id == result.request_id)
            )
        ).scalar_one()
        assert events == 2


@pytest.mark.asyncio
async def test_zero_selection_is_durable_completed_and_owned(factory) -> None:
    result = await _filtered(factory, []).execute(client_id="client", traceparent="")
    status = await QueryInvestigationRequestStatus(
        unit_of_work_factory=lambda: SqlAlchemyInvestigationUnitOfWork(factory)
    ).execute(request_id=result.request_id, client_id="client")
    assert status.status == "completed"
    assert status.summary.total == 0
    assert status.investigations == ()


@pytest.mark.asyncio
async def test_concurrent_requests_create_one_active_investigation_and_outbox(factory) -> None:
    key = uuid.uuid4().hex
    first, second = await asyncio.gather(
        *[_filtered(factory, [key]).execute(client_id="client", traceparent="") for _ in range(2)]
    )
    assert first.investigations[0].investigation_id == second.investigations[0].investigation_id
    assert sum(row.created for result in (first, second) for row in result.investigations) == 1
    async with factory() as session:
        events = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .join(Investigation, OutboxEvent.aggregate_id == Investigation.id)
                .where(Investigation.exception_id == key)
            )
        ).scalar_one()
        assert events == 1


@pytest.mark.asyncio
async def test_completion_between_initial_check_and_insert_leaves_no_new_work(
    factory, monkeypatch
) -> None:
    key = uuid.uuid4().hex
    old = await _explicit(factory, [key])
    original = InvestigationRepository.completed_exception_ids
    check_done = asyncio.Event()
    completed = asyncio.Event()
    reads = 0

    async def controlled_read(self, ids):
        nonlocal reads
        result = await original(self, ids)
        if ids == [key]:
            reads += 1
            if reads == 2:  # First is page history; second is admission's pre-insert check.
                check_done.set()
                await asyncio.wait_for(completed.wait(), timeout=5)
        return result

    async def finish_old():
        await asyncio.wait_for(check_done.wait(), timeout=5)
        async with factory() as session:
            await session.execute(
                update(Investigation)
                .where(Investigation.id == old.investigations[0].investigation_id)
                .values(status="completed")
            )
            await session.commit()
        completed.set()

    monkeypatch.setattr(InvestigationRepository, "completed_exception_ids", controlled_read)
    result, _ = await asyncio.wait_for(
        asyncio.gather(
            _filtered(factory, [key]).execute(client_id="client", traceparent=""), finish_old()
        ),
        timeout=10,
    )
    assert result.investigations == ()
    async with factory() as session:
        assert (await session.get(ApiRequest, result.request_id)).exception_ids == []
        assert (
            await session.execute(
                select(func.count())
                .select_from(Investigation)
                .where(Investigation.exception_id == key)
            )
        ).scalar_one() == 1
        assert (
            await session.execute(
                select(func.count())
                .select_from(ApiRequestInvestigation)
                .where(ApiRequestInvestigation.request_id == result.request_id)
            )
        ).scalar_one() == 0
        assert (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .join(Investigation, OutboxEvent.aggregate_id == Investigation.id)
                .where(Investigation.request_id == result.request_id)
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_ctc_failure_does_not_create_request(factory) -> None:
    class Unavailable:
        async def read_page(self, **kwargs):
            raise CandidateSourceUnavailableError("unavailable")

    async with factory() as session:
        before = (await session.execute(select(func.count()).select_from(ApiRequest))).scalar_one()
    with pytest.raises(CandidateSourceUnavailableError):
        await _filtered(factory, [], Unavailable()).execute(client_id="client", traceparent="")
    async with factory() as session:
        assert (
            await session.execute(select(func.count()).select_from(ApiRequest))
        ).scalar_one() == before


@pytest.mark.asyncio
async def test_outbox_failure_rolls_back_entire_filtered_request(factory, monkeypatch) -> None:
    from orchestrator.db.repositories.outbox import OutboxRepository
    from orchestrator.ports.investigation_store import InvestigationStoreUnavailableError
    from sqlalchemy.exc import OperationalError

    key = uuid.uuid4().hex

    async def fail(self, **kwargs):
        raise OperationalError("insert", {}, OSError("unavailable"))

    monkeypatch.setattr(OutboxRepository, "add_investigation_requested", fail)
    async with factory() as session:
        before = (await session.execute(select(func.count()).select_from(ApiRequest))).scalar_one()
    with pytest.raises(InvestigationStoreUnavailableError):
        await _filtered(factory, [key]).execute(client_id="client", traceparent="")
    async with factory() as session:
        assert (
            await session.execute(select(func.count()).select_from(ApiRequest))
        ).scalar_one() == before
        assert (
            await session.execute(
                select(func.count())
                .select_from(Investigation)
                .where(Investigation.exception_id == key)
            )
        ).scalar_one() == 0
