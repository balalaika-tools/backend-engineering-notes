"""Worker lifecycle ownership against disposable PostgreSQL."""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from platform_db import ApiRequest, Investigation
from platform_db import InvestigationStatus as DatabaseStatus
from sqlalchemy import delete, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from worker.db.platform.repositories import InvestigationRepository
from worker.db.platform.session import build_session_factory
from worker.domain.investigation import InvestigationStatus
from worker.ports.investigation.investigation_store import InvestigationFailureMutation

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
        await value.exec(delete(ApiRequest))
        await value.commit()
        yield value
        await value.rollback()
    await engine.dispose()


async def _seed(
    session: AsyncSession,
    *,
    status: DatabaseStatus = DatabaseStatus.QUEUED,
    worker_id: str | None = None,
    lease_expires_at: datetime | None = None,
) -> uuid.UUID:
    request_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    session.add(ApiRequest(id=request_id, client_id="client", exception_ids=["EX-1"]))
    session.add(
        Investigation(
            id=investigation_id,
            request_id=request_id,
            client_id="client",
            exception_id="EX-1",
            event_id=uuid.uuid4(),
            status=status,
            worker_id=worker_id,
            lease_expires_at=lease_expires_at,
        )
    )
    await session.commit()
    return investigation_id


@pytest.mark.asyncio
async def test_claim_queued_investigation_sets_owner_and_attempt(session: AsyncSession) -> None:
    investigation_id = await _seed(session)
    repository = InvestigationRepository(session)

    claimed = await repository.claim(
        investigation_id,
        worker_id="worker-a",
        lease_seconds=90,
    )
    await session.commit()

    assert claimed is not None
    assert claimed.status is InvestigationStatus.PROCESSING
    assert claimed.worker_id == "worker-a"
    assert claimed.attempt_count == 1
    assert claimed.started_at is not None
    assert claimed.lease_expires_at is not None


@pytest.mark.asyncio
async def test_claim_takes_over_expired_processing_lease(session: AsyncSession) -> None:
    investigation_id = await _seed(
        session,
        status=DatabaseStatus.PROCESSING,
        worker_id="worker-old",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    repository = InvestigationRepository(session)

    claimed = await repository.claim(
        investigation_id,
        worker_id="worker-new",
        lease_seconds=90,
    )

    assert claimed is not None
    assert claimed.worker_id == "worker-new"
    assert claimed.attempt_count == 1


@pytest.mark.asyncio
async def test_claim_rejects_live_processing_lease(session: AsyncSession) -> None:
    investigation_id = await _seed(
        session,
        status=DatabaseStatus.PROCESSING,
        worker_id="worker-live",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    claimed = await InvestigationRepository(session).claim(
        investigation_id,
        worker_id="worker-other",
        lease_seconds=90,
    )

    assert claimed is None


@pytest.mark.asyncio
async def test_completion_updates_zero_rows_after_takeover(session: AsyncSession) -> None:
    investigation_id = await _seed(session)
    repository = InvestigationRepository(session)
    await repository.claim(investigation_id, worker_id="worker-old", lease_seconds=90)
    model = await session.get(Investigation, investigation_id)
    assert model is not None
    model.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    await repository.claim(investigation_id, worker_id="worker-new", lease_seconds=90)

    completed = await repository.complete(investigation_id, worker_id="worker-old")
    current = await repository.get(investigation_id)

    assert completed is False
    assert current is not None
    assert current.worker_id == "worker-new"
    assert current.status is InvestigationStatus.PROCESSING


@pytest.mark.asyncio
async def test_owner_can_checkpoint_write_back_and_complete(session: AsyncSession) -> None:
    investigation_id = await _seed(session)
    repository = InvestigationRepository(session)
    await repository.claim(investigation_id, worker_id="worker-a", lease_seconds=90)

    assert await repository.extend_lease(
        investigation_id,
        worker_id="worker-a",
        lease_seconds=90,
    )
    assert await repository.persist_analysis(
        investigation_id,
        worker_id="worker-a",
        analysis={"reason_code": "repair"},
        report="# report",
        report_uri="s3://reports/report.md",
    )
    assert await repository.record_write_back_step(
        investigation_id,
        worker_id="worker-a",
        step="comment",
        outcome="written",
    )
    assert await repository.record_write_back_step(
        investigation_id,
        worker_id="worker-a",
        step="codes",
        outcome="skipped_version_conflict",
        detail="version conflict",
    )
    assert await repository.complete(investigation_id, worker_id="worker-a")
    await session.commit()
    current = await repository.get(investigation_id)

    assert current is not None
    assert current.status is InvestigationStatus.COMPLETED
    assert current.analysis == {"reason_code": "repair"}
    assert current.comment_outcome == "written"
    assert current.codes_outcome == "skipped_version_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_status",
    [InvestigationStatus.QUEUED, InvestigationStatus.FAILED],
)
async def test_failure_transition_commits_caller_selected_state(
    session: AsyncSession,
    target_status: InvestigationStatus,
) -> None:
    investigation_id = await _seed(
        session,
        status=DatabaseStatus.PROCESSING,
        worker_id="worker-a",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    repository = InvestigationRepository(session)
    completed_at = datetime.now(UTC) if target_status is InvestigationStatus.FAILED else None

    updated = await repository.apply_failure_transition(
        InvestigationFailureMutation(
            investigation_id=investigation_id,
            worker_id="worker-a",
            expected_attempt_count=0,
            target_status=target_status,
            completed_at=completed_at,
            error_code="provider_unavailable",
            error_message="provider down",
        )
    )
    await session.commit()
    current = await repository.get(investigation_id)

    assert updated is True
    assert current is not None
    assert current.status is target_status
    assert current.last_error_code == "provider_unavailable"
    assert current.worker_id is None


@pytest.mark.asyncio
async def test_stale_owner_cannot_apply_failure_transition(session: AsyncSession) -> None:
    investigation_id = await _seed(
        session,
        status=DatabaseStatus.PROCESSING,
        worker_id="worker-new",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    repository = InvestigationRepository(session)

    updated = await repository.apply_failure_transition(
        InvestigationFailureMutation(
            investigation_id=investigation_id,
            worker_id="worker-old",
            expected_attempt_count=0,
            target_status=InvestigationStatus.FAILED,
            completed_at=datetime.now(UTC),
            error_code="internal_error",
            error_message="stale worker",
        )
    )
    current = await repository.get(investigation_id)

    assert updated is False
    assert current is not None
    assert current.status is InvestigationStatus.PROCESSING
    assert current.worker_id == "worker-new"
