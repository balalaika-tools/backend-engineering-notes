"""Outbox publication against PostgreSQL and NATS JetStream."""

import os
import uuid
from collections.abc import AsyncIterator

import nats
import pytest
import pytest_asyncio
from nats.aio.client import Client as NatsClient
from nats.js.client import JetStreamContext
from orchestrator.adapters.nats_publisher import NatsJetStreamPublisher
from orchestrator.application.publish_outbox import PublishOutbox
from orchestrator.db.session import build_session_factory
from orchestrator.db.unit_of_work import SqlAlchemyOutboxUnitOfWork
from orchestrator.ports.event_publisher import EventPublishError
from platform_db import ApiRequest, Investigation, OutboxEvent
from sqlalchemy import delete, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_env(
        "INTEGRATION_PLATFORM_DATABASE_URL",
        "INTEGRATION_NATS_URL",
        "INTEGRATION_NATS_AUTH_TOKEN",
    ),
]


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


class FailingPublisher:
    async def publish(
        self,
        *,
        subject: str,
        payload: object,
        message_id: uuid.UUID,
    ) -> None:
        del subject, payload, message_id
        raise EventPublishError("simulated timeout")


async def _connect_nats() -> NatsClient:
    return await nats.connect(
        _required_env("INTEGRATION_NATS_URL"),
        token=_required_env("INTEGRATION_NATS_AUTH_TOKEN"),
    )


async def _clear_platform_rows(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await session.exec(delete(OutboxEvent))
        await session.exec(delete(Investigation))
        await session.exec(delete(ApiRequest))
        await session.commit()


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(make_url(_required_env("INTEGRATION_PLATFORM_DATABASE_URL")))
    factory = build_session_factory(engine)
    await _clear_platform_rows(factory)
    try:
        yield factory
    finally:
        await _clear_platform_rows(factory)
        await engine.dispose()


@pytest_asyncio.fixture
async def nats_case() -> AsyncIterator[tuple[JetStreamContext, str]]:
    client = await _connect_nats()
    jetstream = client.jetstream()
    suffix = uuid.uuid4().hex[:12]
    stream = f"OUTBOX_VERIFY_{suffix.upper()}"
    subject = f"tests.outbox.{suffix}"
    await jetstream.add_stream(name=stream, subjects=[subject])
    try:
        yield jetstream, subject
    finally:
        await jetstream.delete_stream(stream)
        await client.drain()


@pytest.mark.asyncio
async def test_puback_marks_published_and_timeout_leaves_event_pending(
    nats_case: tuple[JetStreamContext, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    jetstream, subject = nats_case
    subscription = await jetstream.pull_subscribe(
        subject,
        durable=f"outbox-verifier-{uuid.uuid4().hex[:12]}",
    )

    request_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    acknowledged_event_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(ApiRequest(id=request_id, client_id="client", exception_ids=["EX-1"]))
        session.add(
            Investigation(
                id=investigation_id,
                request_id=request_id,
                client_id="client",
                exception_id="EX-1",
                event_id=acknowledged_event_id,
            )
        )
        session.add(
            OutboxEvent(
                event_id=acknowledged_event_id,
                aggregate_type="investigation",
                aggregate_id=investigation_id,
                event_type="investigation.requested",
                subject=subject,
                payload={
                    "event_id": str(acknowledged_event_id),
                    "traceparent": ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"),
                },
            )
        )
        await session.commit()

    action = PublishOutbox(
        unit_of_work_factory=lambda: SqlAlchemyOutboxUnitOfWork(session_factory),
        publisher=NatsJetStreamPublisher(jetstream, timeout_seconds=2),
        batch_size=10,
    )
    result = await action.execute()
    messages = await subscription.fetch(1, timeout=2)

    async with session_factory() as session:
        acknowledged = (
            await session.exec(
                select(OutboxEvent).where(OutboxEvent.event_id == acknowledged_event_id)
            )
        ).one()
    assert result.published == 1
    assert acknowledged.published_at is not None
    assert messages[0].headers is not None
    assert messages[0].headers["Nats-Msg-Id"] == str(acknowledged_event_id)
    assert messages[0].headers["traceparent"].split("-")[1] == ("4bf92f3577b34da6a3ce929d0e0e4736")
    await messages[0].ack()

    pending_event_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            OutboxEvent(
                event_id=pending_event_id,
                aggregate_type="investigation",
                aggregate_id=investigation_id,
                event_type="investigation.requested",
                subject="investigations.requested",
                payload={"event_id": str(pending_event_id)},
            )
        )
        await session.commit()

    failing_action = PublishOutbox(
        unit_of_work_factory=lambda: SqlAlchemyOutboxUnitOfWork(session_factory),
        publisher=FailingPublisher(),
        batch_size=10,
    )
    failed_result = await failing_action.execute()
    async with session_factory() as session:
        pending = (
            await session.exec(select(OutboxEvent).where(OutboxEvent.event_id == pending_event_id))
        ).one()

    assert failed_result.failed == 1
    assert pending.published_at is None
    assert pending.attempt_count == 1
    assert pending.last_error == "simulated timeout"
