"""Reconciler behavior against the disposable platform PostgreSQL."""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
import pytest_asyncio
from platform_db import ApiRequest, ConfigState, Investigation, InvestigationStatus, OutboxEvent
from sqlalchemy import Table, delete, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from worker.application.reconcile import MaxDeliveryOutcome, Reconcile, decide_max_delivery
from worker.db.platform.reconciliation import ReconciliationRepository
from worker.db.platform.reconciliation_uow import SqlAlchemyReconciliationUnitOfWork
from worker.db.platform.repositories import InvestigationRepository
from worker.db.platform.session import build_session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_env("INTEGRATION_PLATFORM_DATABASE_URL"),
]
NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def _database_url() -> str:
    value = os.environ.get("INTEGRATION_PLATFORM_DATABASE_URL")
    if not value:
        pytest.skip("INTEGRATION_PLATFORM_DATABASE_URL is required")
    return value


@pytest_asyncio.fixture
async def platform() -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(make_url(_database_url()))
    factory = build_session_factory(engine)
    yield engine, factory
    await engine.dispose()


def _request(request_id: uuid.UUID, *, age_days: int) -> ApiRequest:
    return ApiRequest(
        id=request_id,
        client_id="reconciliation-test",
        exception_ids=[request_id.hex],
        created_at=NOW - timedelta(days=age_days),
    )


def _investigation(
    investigation_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    status: InvestigationStatus,
    completed_age_days: int | None = None,
    lease_age_minutes: int | None = None,
    lease_expires_at: datetime | None = None,
) -> Investigation:
    return Investigation(
        id=investigation_id,
        request_id=request_id,
        client_id="reconciliation-test",
        exception_id=investigation_id.hex,
        event_id=uuid.uuid4(),
        status=status,
        created_at=NOW - timedelta(days=100),
        completed_at=(
            NOW - timedelta(days=completed_age_days) if completed_age_days is not None else None
        ),
        lease_expires_at=lease_expires_at
        or (NOW - timedelta(minutes=lease_age_minutes) if lease_age_minutes is not None else None),
    )


def _outbox(
    investigation_id: uuid.UUID,
    *,
    published: bool,
) -> OutboxEvent:
    return OutboxEvent(
        event_id=uuid.uuid4(),
        aggregate_type="investigation",
        aggregate_id=investigation_id,
        event_type="investigation.requested",
        subject="reconciliation.test",
        payload={"investigation_id": str(investigation_id)},
        created_at=NOW - timedelta(days=100),
        published_at=NOW - timedelta(days=100) if published else None,
        next_attempt_at=NOW - timedelta(days=100),
    )


@pytest.mark.asyncio
async def test_reconciliation_checks_and_retention_exclusions(
    platform: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = platform
    request_ids = [uuid.uuid4() for _ in range(7)]
    old_terminal_id, active_id, recent_id, exhausted_id, expired_owner_id, live_owner_id = [
        uuid.uuid4() for _ in range(6)
    ]
    old_published = _outbox(old_terminal_id, published=True)
    old_unpublished = _outbox(old_terminal_id, published=False)
    async with factory() as session:
        session.add_all(
            [
                *[_request(value, age_days=100) for value in request_ids],
                _investigation(
                    old_terminal_id,
                    request_ids[0],
                    status=InvestigationStatus.COMPLETED,
                    completed_age_days=100,
                ),
                _investigation(
                    active_id,
                    request_ids[1],
                    status=InvestigationStatus.PROCESSING,
                    lease_age_minutes=11,
                ),
                _investigation(
                    recent_id,
                    request_ids[2],
                    status=InvestigationStatus.COMPLETED,
                    completed_age_days=80,
                ),
                _investigation(
                    exhausted_id,
                    request_ids[3],
                    status=InvestigationStatus.QUEUED,
                ),
                _investigation(
                    expired_owner_id,
                    request_ids[5],
                    status=InvestigationStatus.PROCESSING,
                    lease_age_minutes=1,
                ),
                _investigation(
                    live_owner_id,
                    request_ids[4],
                    status=InvestigationStatus.PROCESSING,
                    lease_expires_at=datetime(2100, 1, 1, tzinfo=UTC),
                ),
                old_published,
                old_unpublished,
            ]
        )
        await session.commit()
        config_state = await session.get(ConfigState, 1)
        assert config_state is not None
        config_generation = config_state.generation
        assert old_published.id is not None
        assert old_unpublished.id is not None

    action = Reconcile(
        unit_of_work_factory=lambda: SqlAlchemyReconciliationUnitOfWork(factory),
        database_retention_days=90,
        clock=lambda: NOW,
    )
    try:
        result = await action.execute((exhausted_id, expired_owner_id, live_owner_id))

        async with factory() as session:
            exhausted = await session.get(Investigation, exhausted_id)
            expired_owner = await session.get(Investigation, expired_owner_id)
            live_owner = await session.get(Investigation, live_owner_id)
            assert await session.get(Investigation, old_terminal_id) is None
            assert await session.get(Investigation, active_id) is not None
            assert await session.get(Investigation, recent_id) is not None
            assert exhausted is not None
            assert exhausted.status is InvestigationStatus.FAILED
            assert exhausted.last_error_code == "MAX_DELIVER_EXCEEDED"
            assert expired_owner is not None
            assert expired_owner.status is InvestigationStatus.FAILED
            assert live_owner is not None
            assert live_owner.status is InvestigationStatus.PROCESSING
            assert await session.get(OutboxEvent, old_published.id) is None
            assert await session.get(OutboxEvent, old_unpublished.id) is not None
            assert await session.get(ApiRequest, request_ids[0]) is None
            assert await session.get(ApiRequest, request_ids[6]) is None
            assert await session.get(ApiRequest, request_ids[1]) is not None
            assert await session.get(ApiRequest, request_ids[2]) is not None
            assert await session.get(ApiRequest, request_ids[3]) is not None
            assert await session.get(ApiRequest, request_ids[4]) is not None
            assert await session.get(ApiRequest, request_ids[5]) is not None
            current_config = await session.get(ConfigState, 1)
            assert current_config is not None
            assert current_config.generation == config_generation

        assert result.stale_processing == 1
        assert result.outbox_lagging
        assert result.max_deliver_exhausted == 2
        assert {item.investigation_id: item.outcome for item in result.investigation_results} == {
            exhausted_id: MaxDeliveryOutcome.FAILED,
            expired_owner_id: MaxDeliveryOutcome.FAILED,
            live_owner_id: MaxDeliveryOutcome.DEFERRED,
        }
        assert result.retention_deletes.outbox_events == 1
        assert result.retention_deletes.investigations == 1
        assert result.retention_deletes.api_requests == 2
    finally:
        outbox_table = cast(Table, OutboxEvent.__table__)  # type: ignore[attr-defined]
        investigation_table = cast(
            Table,
            Investigation.__table__,  # type: ignore[attr-defined]
        )
        request_table = cast(Table, ApiRequest.__table__)  # type: ignore[attr-defined]
        async with factory() as session:
            await session.execute(
                delete(outbox_table).where(
                    outbox_table.c.event_id.in_([old_published.event_id, old_unpublished.event_id])
                )
            )
            await session.execute(
                delete(investigation_table).where(
                    investigation_table.c.id.in_(
                        [
                            old_terminal_id,
                            active_id,
                            recent_id,
                            exhausted_id,
                            expired_owner_id,
                            live_owner_id,
                        ]
                    )
                )
            )
            await session.execute(delete(request_table).where(request_table.c.id.in_(request_ids)))
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("competing_operation", ["claim", "heartbeat"])
async def test_concurrent_owner_change_fences_stale_reconciliation_mutation(
    platform: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    competing_operation: str,
) -> None:
    _, factory = platform
    request_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    initial_status = (
        InvestigationStatus.QUEUED
        if competing_operation == "claim"
        else InvestigationStatus.PROCESSING
    )
    async with factory() as session:
        session.add(_request(request_id, age_days=0))
        session.add(
            _investigation(
                investigation_id,
                request_id,
                status=initial_status,
                lease_age_minutes=1 if competing_operation == "heartbeat" else None,
            )
        )
        if competing_operation == "heartbeat":
            model = await session.get(Investigation, investigation_id)
            assert model is not None
            model.worker_id = "worker-a"
        await session.commit()

    try:
        async with factory() as session:
            observed = (
                await ReconciliationRepository(session).read_for_update((investigation_id,))
            )[0]
            decision = decide_max_delivery(investigation_id, observed)
            assert decision.mutation is not None
            await session.rollback()

        async with factory() as session:
            repository = InvestigationRepository(session)
            if competing_operation == "claim":
                assert await repository.claim(
                    investigation_id,
                    worker_id="worker-a",
                    lease_seconds=90,
                )
            else:
                assert await repository.extend_lease(
                    investigation_id,
                    worker_id="worker-a",
                    lease_seconds=90,
                )
            await session.commit()

        async with factory() as session:
            assert not await ReconciliationRepository(session).apply_mutation(decision.mutation)
            await session.commit()
            current = await session.get(Investigation, investigation_id)
            assert current is not None
            assert current.status is InvestigationStatus.PROCESSING
            assert current.worker_id == "worker-a"
    finally:
        outbox_table = cast(Table, OutboxEvent.__table__)  # type: ignore[attr-defined]
        investigation_table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
        request_table = cast(Table, ApiRequest.__table__)  # type: ignore[attr-defined]
        async with factory() as session:
            await session.exec(
                delete(outbox_table).where(outbox_table.c.aggregate_id == investigation_id)
            )
            await session.exec(
                delete(investigation_table).where(investigation_table.c.id == investigation_id)
            )
            await session.exec(delete(request_table).where(request_table.c.id == request_id))
            await session.commit()


@pytest.mark.asyncio
async def test_reconciliation_mutation_rolls_back_with_failed_pass(
    platform: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = platform
    request_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    async with factory() as session:
        session.add(_request(request_id, age_days=0))
        session.add(_investigation(investigation_id, request_id, status=InvestigationStatus.QUEUED))
        await session.commit()

    try:
        with pytest.raises(RuntimeError, match="abort pass"):
            async with SqlAlchemyReconciliationUnitOfWork(factory) as unit_of_work:
                observed = await unit_of_work.reconciliation.read_for_update((investigation_id,))
                decision = decide_max_delivery(investigation_id, observed[0])
                assert decision.mutation is not None
                assert await unit_of_work.reconciliation.apply_mutation(decision.mutation)
                raise RuntimeError("abort pass")

        async with factory() as session:
            current = await session.get(Investigation, investigation_id)
            assert current is not None
            assert current.status is InvestigationStatus.QUEUED
    finally:
        investigation_table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
        request_table = cast(Table, ApiRequest.__table__)  # type: ignore[attr-defined]
        async with factory() as session:
            await session.exec(
                delete(investigation_table).where(investigation_table.c.id == investigation_id)
            )
            await session.exec(delete(request_table).where(request_table.c.id == request_id))
            await session.commit()
