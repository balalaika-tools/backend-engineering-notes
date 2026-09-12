"""Repository behavior against disposable PostgreSQL."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from orchestrator.application.request_investigations import RequestInvestigations
from orchestrator.db.repositories.config_state import ConfigStateRepository
from orchestrator.db.repositories.investigations import InvestigationRepository
from orchestrator.db.repositories.outbox import OutboxRepository
from orchestrator.db.repositories.requests import RequestRepository
from orchestrator.db.session import build_session_factory
from orchestrator.db.unit_of_work import SqlAlchemyInvestigationUnitOfWork
from platform_db import (
    ApiRequest,
    ApiRequestInvestigation,
    ConfigState,
    Investigation,
    InvestigationStatus,
    OutboxEvent,
)
from sqlalchemy import delete, func, make_url, select, update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_env("INTEGRATION_PLATFORM_DATABASE_URL"),
]


def _database_url() -> str:
    value = os.environ.get("INTEGRATION_PLATFORM_DATABASE_URL")
    if not value:
        pytest.skip("INTEGRATION_PLATFORM_DATABASE_URL is required")
    return value


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(make_url(_database_url()))
    factory = build_session_factory(engine)
    async with factory() as value:
        await value.exec(delete(Investigation))
        await value.commit()
        yield value
        await value.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_insert_on_conflict_attaches_to_active_investigation(
    session: AsyncSession,
) -> None:
    requests = RequestRepository(session)
    investigations = InvestigationRepository(session)
    outbox = OutboxRepository(session)
    first_request_id = await requests.add(client_id="client-a", exception_ids=["EX-1"])
    first = await investigations.insert_or_attach(
        request_id=first_request_id,
        client_id="client-a",
        exception_id="EX-1",
    )
    await requests.attach_investigation(
        request_id=first_request_id,
        investigation_id=first.investigation.id,
        position=0,
    )
    await outbox.add_investigation_requested(
        investigation=first.investigation,
        traceparent="00-test-trace-01",
        subject="investigations.requested",
    )
    await session.commit()

    second_request_id = await requests.add(client_id="client-b", exception_ids=["EX-1"])
    attached = await investigations.insert_or_attach(
        request_id=second_request_id,
        client_id="client-b",
        exception_id="EX-1",
    )
    await requests.attach_investigation(
        request_id=second_request_id,
        investigation_id=attached.investigation.id,
        position=0,
    )
    await session.commit()
    second_batch = await investigations.by_api_request_id(second_request_id)

    assert first.created is True
    assert attached.created is False
    assert attached.investigation.id == first.investigation.id
    assert [item.id for item in second_batch] == [first.investigation.id]


@pytest.mark.asyncio
async def test_most_recent_lookups_return_latest_rerun_regardless_of_status(
    session: AsyncSession,
) -> None:
    requests = RequestRepository(session)
    investigations = InvestigationRepository(session)
    request_id = await requests.add(client_id="client-a", exception_ids=["EX-2"])
    first = await investigations.insert_or_attach(
        request_id=request_id,
        client_id="client-a",
        exception_id="EX-2",
    )
    first_model = await session.get(Investigation, first.investigation.id)
    assert first_model is not None
    first_model.status = InvestigationStatus.COMPLETED
    first_model.completed_at = datetime.now(UTC)
    await session.commit()

    rerun = await investigations.insert_or_attach(
        request_id=request_id,
        client_id="client-a",
        exception_id="EX-2",
        investigation_id=uuid.uuid4(),
    )
    await session.commit()
    found = await investigations.most_recent_by_exception_ids(["EX-2"])
    reports = await investigations.most_recent_reports_by_exception_ids(["EX-2"])

    assert rerun.created is True
    assert found["EX-2"].id == rerun.investigation.id
    assert reports["EX-2"].id == rerun.investigation.id
    assert reports["EX-2"].status.value == "queued"


@pytest.mark.asyncio
async def test_concurrent_identical_requests_create_one_investigation_and_event() -> None:
    engine = create_async_engine(make_url(_database_url()))
    factory = build_session_factory(engine)
    async with factory() as cleanup_session:
        await cleanup_session.exec(delete(OutboxEvent))
        await cleanup_session.exec(delete(Investigation))
        await cleanup_session.exec(delete(ApiRequest))
        await cleanup_session.commit()

    action = RequestInvestigations(
        unit_of_work_factory=lambda: SqlAlchemyInvestigationUnitOfWork(factory),
        max_batch_size=100,
        subject="investigations.requested",
    )
    results = await asyncio.gather(
        action.execute(
            client_id="client-a",
            exception_ids=["EX-CONCURRENT"],
            traceparent="00-first",
        ),
        action.execute(
            client_id="client-b",
            exception_ids=["EX-CONCURRENT"],
            traceparent="00-second",
        ),
    )

    async with factory() as verification_session:
        investigation_count = (
            await verification_session.exec(select(func.count()).select_from(Investigation))
        ).one()[0]
        outbox_count = (
            await verification_session.exec(select(func.count()).select_from(OutboxEvent))
        ).one()[0]
        membership_count = (
            await verification_session.exec(
                select(func.count()).select_from(ApiRequestInvestigation)
            )
        ).one()[0]
    await engine.dispose()

    assert investigation_count == 1
    assert outbox_count == 1
    assert membership_count == 2
    assert sorted(result.investigations[0].created for result in results) == [False, True]


@pytest.mark.asyncio
async def test_configuration_generation_increment_is_atomic(session: AsyncSession) -> None:
    await session.exec(update(ConfigState).where(ConfigState.id == 1).values(generation=0))
    repository = ConfigStateRepository(session)

    first = await repository.increment_generation()
    second = await repository.increment_generation()
    await session.commit()

    assert (first, second) == (1, 2)
