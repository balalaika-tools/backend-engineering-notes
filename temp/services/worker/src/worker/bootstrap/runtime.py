"""Worker composition root and process resource lifecycle."""

import asyncio
import random
import socket
from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx
import nats
from nats.aio.client import Client as NatsClient
from platform_observability import shutdown_observability
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine
from worker.adapters.aws.s3_manual_store import S3ManualStore
from worker.adapters.aws.s3_report_store import S3ReportStore
from worker.adapters.ctc.client import CtcClient
from worker.adapters.ctc.config_reader import CtcConfigReader
from worker.adapters.ctc.write_back import CtcWriteBackAdapter
from worker.adapters.nats.consumer import NatsConsumerSettings, NatsInvestigationConsumer
from worker.adapters.nats.handler import NatsInvestigationHandler
from worker.adapters.nats.max_delivery_advisories import (
    MaxDeliveryCoordinator,
    NatsMaxDeliveryAdvisories,
)
from worker.adapters.redis.control_context_cache import RedisControlContextCache
from worker.adapters.redis.cooldown import RedisCooldownSignal
from worker.adapters.redis.iam_credentials import build_redis_client
from worker.application.investigate_exception import InvestigateException, WriteBackSettings
from worker.application.reconcile import Reconcile
from worker.application.record_investigation_failure import RecordInvestigationFailure
from worker.application.resolve_control_context import (
    ControlContextCachePolicy,
    ResolveControlContext,
)
from worker.bootstrap.genai import build_genai_components
from worker.bootstrap.supervisor import (
    AdmissionSupervisor,
    AimdSupervisor,
    ReconcilerSupervisor,
)
from worker.config.secrets import Secrets, get_secrets
from worker.config.settings import Settings, get_settings
from worker.db.ctc.engine import (
    authenticated_database_url as authenticated_ctc_url,
)
from worker.db.ctc.engine import build_engine as build_ctc_engine
from worker.db.ctc.exception_repository import CtcExceptionRepository
from worker.db.ctc.query_executor import CtcQueryExecutor
from worker.db.platform.config_state import SqlAlchemyConfigGenerationSource
from worker.db.platform.engine import (
    authenticated_database_url as authenticated_platform_url,
)
from worker.db.platform.engine import build_engine as build_platform_engine
from worker.db.platform.lease_manager import SqlAlchemyLeaseManager
from worker.db.platform.reconciliation_uow import SqlAlchemyReconciliationUnitOfWork
from worker.db.platform.session import build_session_factory
from worker.db.platform.unit_of_work import SqlAlchemyInvestigationUnitOfWork
from worker.domain.admission import AdmissionState, AimdPolicy, WindowCounters
from worker.observability.metrics import (
    register_ctc_pool_metric,
    worker_actual_inflight,
    worker_in_cooldown,
    worker_target,
)


@dataclass(slots=True)
class WorkerRuntime:
    admission: AdmissionSupervisor
    aimd: AimdSupervisor
    reconciler: ReconcilerSupervisor
    advisories: NatsMaxDeliveryAdvisories
    nats_client: NatsClient
    redis: Redis
    http_client: httpx.AsyncClient
    platform_engine: AsyncEngine
    ctc_engine: AsyncEngine
    ctc_pool_metric: object
    stop: asyncio.Event
    _cleanup: AsyncExitStack

    async def run(self) -> None:
        await self.advisories.start()
        supervisors = {
            asyncio.create_task(self.admission.run(self.stop), name="admission"),
            asyncio.create_task(self.aimd.run(self.stop), name="aimd"),
            asyncio.create_task(self.reconciler.run(self.stop), name="reconciler"),
        }
        stop_waiter = asyncio.create_task(self.stop.wait(), name="shutdown-signal")
        try:
            done, _ = await asyncio.wait(
                {*supervisors, stop_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            failed = next(
                (
                    task
                    for task in done
                    if task is not stop_waiter
                    and not task.cancelled()
                    and task.exception() is not None
                ),
                None,
            )
            self.stop.set()
            await asyncio.gather(*supervisors, return_exceptions=True)
            if failed is not None:
                raise failed.exception()  # type: ignore[misc]
        finally:
            stop_waiter.cancel()
            await asyncio.gather(stop_waiter, return_exceptions=True)
            await self.close()

    async def close(self) -> None:
        try:
            await self.advisories.close()
        finally:
            await self._cleanup.aclose()


@dataclass(slots=True)
class _WorkerResources:
    platform_engine: AsyncEngine
    ctc_engine: AsyncEngine
    ctc_pool_metric: object
    redis: Redis
    http_client: httpx.AsyncClient
    nats_client: NatsClient
    cleanup: AsyncExitStack

    async def close(self) -> None:
        await self.cleanup.aclose()


async def _drain_nats(client: NatsClient) -> None:
    await client.drain()


async def _build_resources(settings: Settings, secrets: Secrets) -> _WorkerResources:
    cleanup = AsyncExitStack()
    cleanup.callback(shutdown_observability)
    try:
        platform_engine = build_platform_engine(
            database_url=authenticated_platform_url(
                str(settings.platform_database_url),
                secrets.platform_db_password,
            ),
            pool_size=settings.platform_pool_max_size,
        )
        cleanup.push_async_callback(platform_engine.dispose)
        ctc_engine = build_ctc_engine(
            database_url=authenticated_ctc_url(
                str(settings.ctc_database_url),
                secrets.ctc_db_password,
            ),
            pool_size=settings.ctc_pool_max_size,
        )
        cleanup.push_async_callback(ctc_engine.dispose)
        ctc_pool_metric = register_ctc_pool_metric(ctc_engine)
        redis = build_redis_client(
            url=str(settings.redis_url),
            iam_auth_enabled=settings.redis_iam_auth_enabled,
            iam_user_id=settings.redis_iam_user_id,
            cache_name=settings.redis_cache_name,
            region=settings.redis_region,
        )
        cleanup.push_async_callback(redis.aclose)
        http_client = httpx.AsyncClient(timeout=30)
        cleanup.push_async_callback(http_client.aclose)
        nats_client = await asyncio.wait_for(
            nats.connect(
                str(settings.nats_url),
                token=secrets.nats_auth_token.get_secret_value(),
                connect_timeout=2,
                max_reconnect_attempts=-1,
            ),
            timeout=10,
        )
        cleanup.push_async_callback(_drain_nats, nats_client)
    except BaseException:
        await cleanup.aclose()
        raise
    return _WorkerResources(
        platform_engine=platform_engine,
        ctc_engine=ctc_engine,
        ctc_pool_metric=ctc_pool_metric,
        redis=redis,
        http_client=http_client,
        nats_client=nats_client,
        cleanup=cleanup.pop_all(),
    )


def _compose_runtime(
    *,
    resolved: Settings,
    credentials: Secrets,
    resources: _WorkerResources,
) -> WorkerRuntime:
    platform_engine = resources.platform_engine
    ctc_engine = resources.ctc_engine
    ctc_pool_metric = resources.ctc_pool_metric
    sessions = build_session_factory(platform_engine)
    redis = resources.redis
    http_client = resources.http_client
    nats_client = resources.nats_client
    jetstream = nats_client.jetstream()

    counters = WindowCounters()
    ctc_client = CtcClient(
        http_client=http_client,
        issuer_url=str(resolved.ctc_issuer_url),
        api_base_url=str(resolved.ctc_api_base_url),
        client_id=resolved.ctc_client_id,
        client_secret=credentials.ctc_client_secret.get_secret_value(),
    )
    genai = build_genai_components(
        settings=resolved,
        counters=counters,
        query_executor=CtcQueryExecutor(
            ctc_engine,
            row_limit=resolved.ctc_query_row_limit,
            statement_timeout_seconds=resolved.ctc_statement_timeout_seconds,
            work_mem_bytes=int(resolved.ctc_work_mem),
        ),
    )
    context_resolver = ResolveControlContext(
        generation_source=SqlAlchemyConfigGenerationSource(sessions),
        context_cache=RedisControlContextCache(
            redis,
            prefix=resolved.control_context_cache_prefix,
        ),
        config_source=CtcConfigReader(ctc_client),
        manual_store=S3ManualStore(
            bucket=resolved.s3_bucket,
            prefix=resolved.manual_key_prefix,
            endpoint_url=_optional_url(resolved.s3_endpoint),
            access_key_id=_secret_value(credentials.s3_access_key_id),
            secret_access_key=_secret_value(credentials.s3_secret_access_key),
        ),
        summarizer=genai.summarizer,
        tenant_token=resolved.tenant_token,
        positions_control=resolved.positions_control,
        reference_control=resolved.reference_control,
        prompt_version=resolved.summarizer_prompt_version,
        deployment_release_id=resolved.deployment_release_id,
        cache_policy=ControlContextCachePolicy(
            rebuild_lease_seconds=resolved.control_context_rebuild_lease_seconds,
            rebuild_wait_seconds=resolved.control_context_rebuild_wait_seconds,
            poll_interval_seconds=resolved.control_context_poll_interval_seconds,
        ),
    )
    worker_id = resolved.service_instance_id or socket.gethostname()
    action = InvestigateException(
        unit_of_work_factory=lambda: SqlAlchemyInvestigationUnitOfWork(sessions),
        context_resolver=context_resolver,
        exception_source=CtcExceptionRepository(ctc_engine),
        analyst=genai.analyst,
        report_store=S3ReportStore(
            bucket=resolved.s3_bucket,
            prefix=resolved.report_key_prefix,
            endpoint_url=_optional_url(resolved.s3_endpoint),
            access_key_id=_secret_value(credentials.s3_access_key_id),
            secret_access_key=_secret_value(credentials.s3_secret_access_key),
        ),
        write_back=CtcWriteBackAdapter(ctc_client),
        write_back_settings=WriteBackSettings(
            tenant_token=resolved.tenant_token,
            control_name=resolved.positions_control,
            exception_name=resolved.exception_name,
            record_comment_limit=resolved.record_comment_limit,
            comment_enabled=resolved.comment_write_enabled,
            codes_enabled=resolved.codes_write_enabled,
        ),
        report_inline_limit=int(resolved.report_inline_limit),
    )
    leases = SqlAlchemyLeaseManager(sessions)
    failure_recorder = RecordInvestigationFailure(
        unit_of_work_factory=lambda: SqlAlchemyInvestigationUnitOfWork(sessions),
        max_attempts=resolved.max_attempts,
    )
    handler = NatsInvestigationHandler(
        action=action,
        delivery_state=leases,
        failure_recorder=failure_recorder,
        worker_id=worker_id,
        lease_seconds=resolved.lease_seconds,
        heartbeat_interval_seconds=resolved.heartbeat_interval_seconds,
        retry_delays_seconds=tuple(resolved.investigation_retry_delays_seconds),
    )
    consumer = NatsInvestigationConsumer(
        jetstream,
        settings=NatsConsumerSettings(
            stream=resolved.nats_stream,
            consumer=resolved.nats_consumer,
            subject=resolved.nats_subject,
            ack_wait_seconds=resolved.ack_wait_seconds,
            max_deliver=resolved.max_deliver,
            max_ack_pending=resolved.max_ack_pending,
            duplicate_window_seconds=resolved.duplicate_window_seconds,
        ),
    )
    cooldown = RedisCooldownSignal(redis)
    admission_state = AdmissionState(
        initial_target=resolved.initial_target,
        max_per_instance=resolved.max_per_instance,
    )
    worker_actual_inflight.set(admission_state.actual_inflight)
    worker_target.set(admission_state.target)
    worker_in_cooldown.set(0)
    advisories = NatsMaxDeliveryAdvisories(
        nats_client,
        jetstream,
        stream=resolved.nats_stream,
        consumer=resolved.nats_consumer,
    )
    reconcile = Reconcile(
        unit_of_work_factory=lambda: SqlAlchemyReconciliationUnitOfWork(sessions),
        database_retention_days=resolved.database_retention_days,
    )
    max_delivery_coordinator = MaxDeliveryCoordinator(advisories, reconcile.execute)
    return WorkerRuntime(
        admission=AdmissionSupervisor(
            consumer=consumer,
            cooldown=cooldown,
            state=admission_state,
            handler=handler.handle,
            fetch_timeout_seconds=resolved.fetch_timeout_seconds,
            interruption_recorder=failure_recorder,
            worker_id=worker_id,
            shutdown_grace_seconds=resolved.shutdown_grace_seconds,
        ),
        aimd=AimdSupervisor(
            state=admission_state,
            counters=counters,
            cooldown=cooldown,
            policy=AimdPolicy(
                min_samples=resolved.min_samples,
                stable_threshold=resolved.stable_threshold,
                decrease_threshold=resolved.decrease_threshold,
                break_threshold=resolved.break_threshold,
                decrease_factor=resolved.decrease_factor,
                min_inflight=resolved.min_inflight,
                max_per_instance=resolved.max_per_instance,
            ),
            window_seconds=resolved.window_seconds,
            cooldown_seconds=resolved.cooldown_seconds,
            increase_step=lambda: random.randint(
                resolved.increase_step_min,
                resolved.increase_step_max,
            ),
        ),
        reconciler=ReconcilerSupervisor(
            max_delivery_coordinator.run_once,
            interval_seconds=resolved.reconcile_interval_seconds,
        ),
        advisories=advisories,
        nats_client=nats_client,
        redis=redis,
        http_client=http_client,
        platform_engine=platform_engine,
        ctc_engine=ctc_engine,
        ctc_pool_metric=ctc_pool_metric,
        stop=asyncio.Event(),
        _cleanup=resources.cleanup,
    )


async def build_runtime(
    *,
    settings: Settings | None = None,
    secrets: Secrets | None = None,
) -> WorkerRuntime:
    resolved = settings or get_settings()
    credentials = secrets or get_secrets()
    resources = await _build_resources(resolved, credentials)
    try:
        return _compose_runtime(
            resolved=resolved,
            credentials=credentials,
            resources=resources,
        )
    except BaseException:
        await resources.close()
        raise


def _optional_url(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _secret_value(value: object | None) -> str | None:
    getter = getattr(value, "get_secret_value", None)
    return str(getter()) if getter is not None else None
