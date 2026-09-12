"""Durable claim and acknowledgement behavior against PostgreSQL and JetStream."""

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import nats
import pytest
import pytest_asyncio
from nats.aio.client import Client
from nats.js.client import JetStreamContext
from platform_db import ApiRequest, Investigation
from platform_db import InvestigationStatus as DatabaseStatus
from sqlalchemy import Table, delete, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from worker.adapters.nats.consumer import NatsConsumerSettings, NatsInvestigationConsumer
from worker.adapters.nats.handler import NatsInvestigationHandler
from worker.application.record_investigation_failure import RecordInvestigationFailure
from worker.db.platform.lease_manager import SqlAlchemyLeaseManager
from worker.db.platform.repositories import InvestigationRepository
from worker.db.platform.session import build_session_factory
from worker.db.platform.unit_of_work import SqlAlchemyInvestigationUnitOfWork

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_env(
        "INTEGRATION_PLATFORM_DATABASE_URL",
        "INTEGRATION_NATS_URL",
        "INTEGRATION_NATS_AUTH_TOKEN",
    ),
]


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


@pytest_asyncio.fixture
async def platform() -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(
        make_url(_required_environment("INTEGRATION_PLATFORM_DATABASE_URL"))
    )
    factory = build_session_factory(engine)
    yield engine, factory
    await engine.dispose()


async def _seed(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: DatabaseStatus,
    worker_id: str | None = None,
    lease_expires_at: datetime | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    request_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    event_id = uuid.uuid4()
    exception_id = f"EX-{investigation_id.hex}"
    async with factory() as session:
        session.add(ApiRequest(id=request_id, client_id="client", exception_ids=[exception_id]))
        session.add(
            Investigation(
                id=investigation_id,
                request_id=request_id,
                client_id="client",
                exception_id=exception_id,
                event_id=event_id,
                status=status,
                worker_id=worker_id,
                lease_expires_at=lease_expires_at,
            )
        )
        await session.commit()
    return investigation_id, request_id, event_id


async def _delete_seeded(
    factory: async_sessionmaker[AsyncSession],
    *,
    investigation_id: uuid.UUID,
    request_id: uuid.UUID,
) -> None:
    investigation_table = cast(Table, Investigation.__table__)  # type: ignore[attr-defined]
    request_table = cast(Table, ApiRequest.__table__)  # type: ignore[attr-defined]
    async with factory() as session:
        await session.exec(
            delete(investigation_table).where(investigation_table.c.id == investigation_id)
        )
        await session.exec(delete(request_table).where(request_table.c.id == request_id))
        await session.commit()


def _payload(investigation_id: uuid.UUID, request_id: uuid.UUID, event_id: uuid.UUID) -> bytes:
    return json.dumps(
        {
            "event_id": str(event_id),
            "event_type": "investigation.requested",
            "occurred_at": datetime.now(UTC).isoformat(),
            "investigation_id": str(investigation_id),
            "request_id": str(request_id),
            "client_id": "client",
            "exception_id": f"EX-{investigation_id.hex}",
        }
    ).encode()


class CompletingAction:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        crash_after_commit: bool = False,
    ) -> None:
        self._factory = factory
        self._crash_after_commit = crash_after_commit
        self.calls = 0

    async def execute(self, *, investigation_id: uuid.UUID, worker_id: str) -> None:
        self.calls += 1
        async with self._factory() as session:
            completed = await InvestigationRepository(session).complete(
                investigation_id,
                worker_id=worker_id,
            )
            assert completed
            await session.commit()
        if self._crash_after_commit:
            raise OSError("simulated crash after terminal commit")


async def _consumer_and_client() -> tuple[
    Client,
    JetStreamContext,
    NatsInvestigationConsumer,
    NatsConsumerSettings,
]:
    client = await nats.connect(
        _required_environment("INTEGRATION_NATS_URL"),
        token=_required_environment("INTEGRATION_NATS_AUTH_TOKEN"),
    )
    jetstream = client.jetstream()
    suffix = uuid.uuid4().hex[:12]
    settings = NatsConsumerSettings(
        stream=f"DELIVERY_VERIFY_{suffix.upper()}",
        consumer=f"delivery-verifier-{suffix}",
        subject=f"tests.delivery.{suffix}",
        ack_wait_seconds=1,
        max_deliver=6,
        max_ack_pending=1,
        duplicate_window_seconds=10,
    )
    consumer = NatsInvestigationConsumer(
        jetstream,
        settings=settings,
    )
    await consumer.ensure()
    return client, jetstream, consumer, settings


def _handler(
    action: CompletingAction,
    factory: async_sessionmaker[AsyncSession],
) -> NatsInvestigationHandler:
    leases = SqlAlchemyLeaseManager(factory)
    return NatsInvestigationHandler(
        action=action,
        delivery_state=leases,
        failure_recorder=RecordInvestigationFailure(
            unit_of_work_factory=lambda: SqlAlchemyInvestigationUnitOfWork(factory),
            max_attempts=5,
        ),
        worker_id="integration-worker",
        lease_seconds=10,
        heartbeat_interval_seconds=5,
        retry_delays_seconds=(0.01,),
        retry_multiplier=lambda: 1,
    )


@pytest.mark.asyncio
async def test_commit_then_crash_redelivery_short_circuits_completed_work(
    platform: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = platform
    investigation_id, request_id, event_id = await _seed(
        factory,
        status=DatabaseStatus.QUEUED,
    )
    client, jetstream, consumer, settings = await _consumer_and_client()
    action = CompletingAction(factory, crash_after_commit=True)
    handler = _handler(action, factory)
    try:
        await jetstream.publish(
            settings.subject,
            _payload(investigation_id, request_id, event_id),
        )
        first = await consumer.fetch_one(timeout_seconds=2)
        assert first is not None
        await handler.handle(first)
        redelivery = await consumer.fetch_one(timeout_seconds=2)
        assert redelivery is not None
        await handler.handle(redelivery)

        async with factory() as session:
            state = await InvestigationRepository(session).get(investigation_id)
        assert state is not None
        assert state.status.value == "completed"
        assert action.calls == 1
    finally:
        await jetstream.delete_stream(settings.stream)
        await client.drain()
        await _delete_seeded(
            factory,
            investigation_id=investigation_id,
            request_id=request_id,
        )


@pytest.mark.asyncio
async def test_expired_processing_lease_is_reclaimed_and_completed(
    platform: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = platform
    investigation_id, request_id, event_id = await _seed(
        factory,
        status=DatabaseStatus.PROCESSING,
        worker_id="dead-worker",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    client, jetstream, consumer, settings = await _consumer_and_client()
    action = CompletingAction(factory)
    handler = _handler(action, factory)
    try:
        await jetstream.publish(
            settings.subject,
            _payload(investigation_id, request_id, event_id),
        )
        message = await consumer.fetch_one(timeout_seconds=2)
        assert message is not None
        await handler.handle(message)

        async with factory() as session:
            state = await InvestigationRepository(session).get(investigation_id)
        assert state is not None
        assert state.status.value == "completed"
        assert state.attempt_count == 1
        assert action.calls == 1
    finally:
        await jetstream.delete_stream(settings.stream)
        await client.drain()
        await _delete_seeded(
            factory,
            investigation_id=investigation_id,
            request_id=request_id,
        )
