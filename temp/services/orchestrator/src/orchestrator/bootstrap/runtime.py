"""Construction and disposal of orchestrator runtime resources."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

import ctc_database
import httpx
from orchestrator.adapters.cognito_jwks_verifier import JwksTokenVerifier
from orchestrator.adapters.nats_publisher import NatsEventPublisher
from orchestrator.adapters.s3_manual_store import S3ManualStore
from orchestrator.application.fetch_reports import FetchReports
from orchestrator.application.publish_outbox import PublishOutbox
from orchestrator.application.query_status import QueryInvestigationRequestStatus, QueryStatus
from orchestrator.application.request_filtered_investigations import RequestFilteredInvestigations
from orchestrator.application.request_investigations import RequestInvestigations
from orchestrator.application.reset_configuration import ResetConfiguration
from orchestrator.bootstrap.supervisor import OutboxPublisherSupervisor
from orchestrator.config.secrets import Secrets, get_secrets
from orchestrator.config.settings import Settings, get_settings
from orchestrator.db.ctc_candidate_source import CtcCandidateSource
from orchestrator.db.engine import authenticated_database_url, build_engine
from orchestrator.db.session import build_session_factory
from orchestrator.db.unit_of_work import (
    SqlAlchemyConfigUnitOfWork,
    SqlAlchemyInvestigationUnitOfWork,
    SqlAlchemyOutboxUnitOfWork,
)
from orchestrator.ports.manual_store import ManualStore
from orchestrator.ports.token_verifier import TokenVerifier
from platform_observability import shutdown_observability
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession


class RuntimeState(Protocol):
    token_verifier: TokenVerifier
    actions: "ApplicationActions"

    async def database_ready(self) -> bool: ...

    def publisher_ready(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ApplicationActions:
    request_filtered_investigations: RequestFilteredInvestigations
    request_investigations: RequestInvestigations
    query_status: QueryStatus
    query_request_status: QueryInvestigationRequestStatus
    fetch_reports: FetchReports
    reset_configuration: ResetConfiguration


@dataclass(slots=True)
class Runtime:
    """Long-lived dependencies shared by request-scoped application work."""

    token_verifier: TokenVerifier
    actions: ApplicationActions
    _ctc_engine: AsyncEngine
    _engine: AsyncEngine
    _session_factory: async_sessionmaker[AsyncSession]
    _http_client: httpx.AsyncClient
    _manual_store: ManualStore
    _publisher: NatsEventPublisher
    _publisher_supervisor: OutboxPublisherSupervisor

    async def database_ready(self) -> bool:
        try:
            async with self._session_factory() as session:
                await session.execute(text("SELECT 1"))
        except (OSError, SQLAlchemyError):
            return False
        return True

    def publisher_ready(self) -> bool:
        return self._publisher_supervisor.ready


def _compose_runtime(
    settings: Settings,
    secrets: Secrets,
    engine: AsyncEngine,
    ctc_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
) -> Runtime:
    token_verifier = JwksTokenVerifier(
        issuer=str(settings.cognito_issuer),
        audience=settings.cognito_audience,
        cache_ttl_seconds=settings.jwks_cache_ttl_seconds,
        client=http_client,
    )
    manual_store = S3ManualStore(
        bucket=settings.s3_bucket,
        prefix=settings.manual_key_prefix,
        endpoint_url=str(settings.s3_endpoint) if settings.s3_endpoint else None,
        access_key_id=(
            secrets.s3_access_key_id.get_secret_value() if secrets.s3_access_key_id else None
        ),
        secret_access_key=(
            secrets.s3_secret_access_key.get_secret_value()
            if secrets.s3_secret_access_key
            else None
        ),
    )
    session_factory = build_session_factory(engine)
    publisher = NatsEventPublisher(
        str(settings.nats_url),
        token=secrets.nats_auth_token.get_secret_value(),
    )
    publisher_supervisor = OutboxPublisherSupervisor(
        PublishOutbox(
            unit_of_work_factory=lambda: SqlAlchemyOutboxUnitOfWork(session_factory),
            publisher=publisher,
            batch_size=settings.outbox_batch_size,
        ),
        poll_interval_seconds=settings.outbox_poll_interval_seconds,
    )

    def investigation_uow() -> SqlAlchemyInvestigationUnitOfWork:
        return SqlAlchemyInvestigationUnitOfWork(session_factory)

    actions = ApplicationActions(
        request_filtered_investigations=RequestFilteredInvestigations(
            candidates=CtcCandidateSource(
                ctc_database.CtcCandidateReader(
                    ctc_engine, statement_timeout_seconds=settings.ctc_statement_timeout_seconds
                )
            ),
            unit_of_work_factory=investigation_uow,
            schema=settings.ctc_reconciliation_schema,
            exception_name=settings.exception_name,
            max_exceptions=settings.max_bulk_exceptions,
            page_size=settings.ctc_scan_page_size,
            subject=settings.nats_subject,
        ),
        request_investigations=RequestInvestigations(
            unit_of_work_factory=investigation_uow,
            max_batch_size=settings.max_investigation_batch_size,
            subject=settings.nats_subject,
        ),
        query_status=QueryStatus(
            unit_of_work_factory=investigation_uow,
            max_batch_size=settings.max_investigation_batch_size,
        ),
        query_request_status=QueryInvestigationRequestStatus(
            unit_of_work_factory=investigation_uow,
        ),
        fetch_reports=FetchReports(
            unit_of_work_factory=investigation_uow,
            max_batch_size=settings.max_report_batch_size,
            inline_limit_bytes=int(settings.report_inline_limit),
        ),
        reset_configuration=ResetConfiguration(
            unit_of_work_factory=lambda: SqlAlchemyConfigUnitOfWork(session_factory),
            manual_store=manual_store,
        ),
    )
    return Runtime(
        token_verifier=token_verifier,
        actions=actions,
        _engine=engine,
        _ctc_engine=ctc_engine,
        _session_factory=session_factory,
        _http_client=http_client,
        _manual_store=manual_store,
        _publisher=publisher,
        _publisher_supervisor=publisher_supervisor,
    )


@asynccontextmanager
async def runtime(
    settings: Settings | None = None,
    secrets: Secrets | None = None,
) -> AsyncIterator[Runtime]:
    resolved_settings = settings or get_settings()
    resolved_secrets = secrets or get_secrets()
    database_url = authenticated_database_url(
        str(resolved_settings.platform_database_url),
        resolved_secrets.platform_db_password,
    )
    async with AsyncExitStack() as stack:
        stack.callback(shutdown_observability)
        engine = build_engine(
            database_url=database_url, pool_size=resolved_settings.platform_pool_max_size
        )
        stack.push_async_callback(engine.dispose)
        ctc_engine = ctc_database.build_engine(
            database_url=ctc_database.authenticated_database_url(
                str(resolved_settings.ctc_database_url), resolved_secrets.ctc_db_password
            ),
            pool_size=resolved_settings.ctc_pool_max_size,
            acquisition_timeout_seconds=resolved_settings.ctc_acquisition_timeout_seconds,
        )
        stack.push_async_callback(ctc_engine.dispose)
        http_client = httpx.AsyncClient(timeout=10)
        stack.push_async_callback(http_client.aclose)
        value = _compose_runtime(
            resolved_settings, resolved_secrets, engine, ctc_engine, http_client
        )
        stack.push_async_callback(value._publisher.close)
        stack.push_async_callback(value._publisher_supervisor.stop)
        value._publisher_supervisor.start()
        yield value
