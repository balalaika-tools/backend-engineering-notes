"""Checkpointed investigation execution independent of queue delivery mechanics."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from worker.application.investigation_checkpoints import (
    CODES_DONE,
    COMMENT_DONE,
    analysis_from_checkpoint,
    checkpoint_exception_version,
    completed_checkpoint,
    require_successful_outcome,
    write_back_complete,
)
from worker.application.resolve_control_context import (
    ControlContextRebuildTimeoutError,
    VocabularyUnavailableError,
)
from worker.domain.analysis import (
    CodeVocabulary,
    ExceptionAnalysis,
    InvalidAnalysisOutputError,
    validate_codes,
)
from worker.domain.comment import render_comment, validate_comment_length
from worker.domain.control_context import ControlContext
from worker.domain.investigation import (
    InvestigationOwnershipLostError,
    InvestigationState,
    PermanentInvestigationError,
    TransientInvestigationError,
)
from worker.domain.report import render_report
from worker.ports.control_context.config_generation import ConfigGenerationUnavailableError
from worker.ports.control_context.config_summarizer import (
    ConfigSummarizationFailedError,
    ConfigSummarizationUnavailableError,
    InvalidConfigSummaryError,
)
from worker.ports.control_context.control_config_source import (
    ControlConfigUnavailableError,
    InvalidControlConfigError,
)
from worker.ports.control_context.control_context_cache import ControlContextCacheUnavailableError
from worker.ports.control_context.manual_store import ManualStoreUnavailableError
from worker.ports.investigation.ctc_write_back import (
    CtcWriteBack,
    CtcWriteBackUnavailableError,
    WriteBackOutcome,
)
from worker.ports.investigation.exception_analyst import (
    AnalysisFailedError,
    AnalysisUnavailableError,
    ExceptionAnalysisInput,
    ExceptionAnalyst,
    RunLimitExceededError,
)
from worker.ports.investigation.exception_source import (
    ExceptionData,
    ExceptionNotFoundError,
    ExceptionSource,
    ExceptionSourceUnavailableError,
    NoLinkedRecordsError,
)
from worker.ports.investigation.investigation_store import (
    InvestigationStoreUnavailableError,
    InvestigationUnitOfWork,
    WriteBackStep,
)
from worker.ports.investigation.report_store import ReportStore, ReportStoreUnavailableError


class ControlContextResolver(Protocol):
    async def resolve(self) -> ControlContext: ...


@dataclass(frozen=True, slots=True)
class WriteBackSettings:
    tenant_token: str
    control_name: str
    exception_name: str
    record_comment_limit: int
    comment_enabled: bool
    codes_enabled: bool


class InvestigateException:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], InvestigationUnitOfWork],
        context_resolver: ControlContextResolver,
        exception_source: ExceptionSource,
        analyst: ExceptionAnalyst,
        report_store: ReportStore,
        write_back: CtcWriteBack,
        write_back_settings: WriteBackSettings,
        report_inline_limit: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._context_resolver = context_resolver
        self._exception_source = exception_source
        self._analyst = analyst
        self._report_store = report_store
        self._write_back = write_back
        self._write_back_settings = write_back_settings
        self._report_inline_limit = report_inline_limit
        self._clock = clock

    async def execute(self, *, investigation_id: uuid.UUID, worker_id: str) -> None:
        try:
            state = await self._load_owned(investigation_id, worker_id=worker_id)
            if write_back_complete(state):
                await self._complete(investigation_id, worker_id=worker_id)
                return
            context = await self._context_resolver.resolve()
            source_data = await self._exception_source.fetch(
                state.exception_id,
                rec_schema=context.positions_rec_schema,
            )
            analysis = await self._resolve_analysis(
                state=state,
                source_data=source_data,
                context=context,
                worker_id=worker_id,
            )
            await self._write_codes(
                state=state,
                source_data=source_data,
                analysis=analysis,
                context=context,
                worker_id=worker_id,
            )
            await self._write_comment(
                state=state,
                source_data=source_data,
                analysis=analysis,
                worker_id=worker_id,
            )
            await self._complete(investigation_id, worker_id=worker_id)
        except (TransientInvestigationError, PermanentInvestigationError):
            raise
        except _TRANSIENT_FAILURES as exc:
            raise TransientInvestigationError(
                str(exc),
                error_code=getattr(exc, "error_code", "external_dependency_unavailable"),
            ) from exc
        except _PERMANENT_FAILURES as exc:
            raise PermanentInvestigationError(
                str(exc),
                error_code=_permanent_error_code(exc),
            ) from exc

    async def _load_owned(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
    ) -> InvestigationState:
        async with self._unit_of_work_factory() as unit_of_work:
            return _require_owned(
                await unit_of_work.investigations.get(investigation_id),
                investigation_id=investigation_id,
                worker_id=worker_id,
            )

    async def _resolve_analysis(
        self,
        *,
        state: InvestigationState,
        source_data: ExceptionData,
        context: ControlContext,
        worker_id: str,
    ) -> ExceptionAnalysis:
        if state.analysis_persisted_at is not None:
            return analysis_from_checkpoint(state.analysis)
        if state.analysis is not None:
            raise PermanentInvestigationError(
                "Investigation has analysis without a persisted checkpoint",
                error_code="invalid_analysis_checkpoint",
            )
        generated_at = self._clock()
        analysis = await self._analyst.analyze(
            ExceptionAnalysisInput(
                exception_id=state.exception_id,
                current_datetime=generated_at,
                exception=source_data.exception_view,
                linked_records=tuple(record.agent_view for record in source_data.records),
            ),
            context=context,
        )
        _post_validate(
            analysis,
            context=context,
            comment_limit=self._write_back_settings.record_comment_limit,
        )
        report = render_report(
            exception_id=state.exception_id,
            analysis=analysis,
            generated_at=generated_at,
        )
        report_uri = await self._report_store.store(
            investigation_id=state.id,
            generated_at=generated_at,
            report=report,
        )
        inline_report = report if len(report.encode("utf-8")) <= self._report_inline_limit else None
        async with self._unit_of_work_factory() as unit_of_work:
            checkpoint = cast(dict[str, object], asdict(analysis))
            checkpoint["_exception_version"] = source_data.exception_version
            await _require_update(
                unit_of_work.investigations.persist_analysis(
                    state.id,
                    worker_id=worker_id,
                    analysis=checkpoint,
                    report=inline_report,
                    report_uri=report_uri,
                )
            )
            await unit_of_work.commit()
        return analysis

    async def _write_comment(
        self,
        *,
        state: InvestigationState,
        source_data: ExceptionData,
        analysis: ExceptionAnalysis,
        worker_id: str,
    ) -> WriteBackOutcome:
        existing = completed_checkpoint(state.comment_outcome, allowed=COMMENT_DONE)
        if existing is not None:
            return existing
        if not self._write_back_settings.comment_enabled:
            return await self._record_outcome(
                state=state,
                worker_id=worker_id,
                step="comment",
                outcome=WriteBackOutcome.SKIPPED_DISABLED,
            )
        result = await self._write_back.write_comment(
            tenant_token=self._write_back_settings.tenant_token,
            control_name=self._write_back_settings.control_name,
            comment=render_comment(
                analysis,
                record_comment_limit=self._write_back_settings.record_comment_limit,
            ),
            record_pks=tuple(record.pk for record in source_data.records),
        )
        outcome = await self._record_outcome(
            state=state,
            worker_id=worker_id,
            step="comment",
            outcome=result.outcome,
            detail=result.detail,
        )
        return require_successful_outcome(outcome, allowed=COMMENT_DONE)

    async def _write_codes(
        self,
        *,
        state: InvestigationState,
        source_data: ExceptionData,
        analysis: ExceptionAnalysis,
        context: ControlContext,
        worker_id: str,
    ) -> WriteBackOutcome:
        existing = completed_checkpoint(state.codes_outcome, allowed=CODES_DONE)
        if existing is not None:
            return existing
        if not self._write_back_settings.codes_enabled:
            return await self._record_outcome(
                state=state,
                worker_id=worker_id,
                step="codes",
                outcome=WriteBackOutcome.SKIPPED_DISABLED,
            )
        result = await self._write_back.write_codes(
            tenant_token=self._write_back_settings.tenant_token,
            control_name=self._write_back_settings.control_name,
            exception_name=self._write_back_settings.exception_name,
            exception_pk=source_data.exception_pk,
            exception_version=checkpoint_exception_version(state, source_data),
            reason_code_feature_id=context.reason_code_feature_id,
            resolution_code_feature_id=context.resolution_code_feature_id,
            reason_code=analysis.reason_code,
            resolution_code=analysis.resolution_code,
        )
        outcome = await self._record_outcome(
            state=state,
            worker_id=worker_id,
            step="codes",
            outcome=result.outcome,
            detail=result.detail,
        )
        return require_successful_outcome(outcome, allowed=CODES_DONE)

    async def _record_outcome(
        self,
        *,
        state: InvestigationState,
        worker_id: str,
        step: WriteBackStep,
        outcome: WriteBackOutcome,
        detail: str | None = None,
    ) -> WriteBackOutcome:
        async with self._unit_of_work_factory() as unit_of_work:
            await _require_update(
                unit_of_work.investigations.record_write_back_step(
                    state.id,
                    worker_id=worker_id,
                    step=step,
                    outcome=outcome.value,
                    detail=detail,
                )
            )
            await unit_of_work.commit()
        if outcome is WriteBackOutcome.FAILED:
            raise PermanentInvestigationError(
                detail or "CTC rejected write-back",
                error_code="write_back_rejected",
            )
        return outcome

    async def _complete(self, investigation_id: uuid.UUID, *, worker_id: str) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            await _require_update(
                unit_of_work.investigations.complete(
                    investigation_id,
                    worker_id=worker_id,
                )
            )
            await unit_of_work.commit()


_TRANSIENT_FAILURES = (
    AnalysisUnavailableError,
    ConfigGenerationUnavailableError,
    ConfigSummarizationUnavailableError,
    ControlConfigUnavailableError,
    ControlContextCacheUnavailableError,
    ControlContextRebuildTimeoutError,
    CtcWriteBackUnavailableError,
    ExceptionSourceUnavailableError,
    ManualStoreUnavailableError,
    ReportStoreUnavailableError,
    InvestigationStoreUnavailableError,
)
_PERMANENT_FAILURES = (
    AnalysisFailedError,
    ConfigSummarizationFailedError,
    ExceptionNotFoundError,
    InvalidAnalysisOutputError,
    InvalidConfigSummaryError,
    InvalidControlConfigError,
    NoLinkedRecordsError,
    RunLimitExceededError,
    VocabularyUnavailableError,
)


async def _require_update(update: Awaitable[bool]) -> None:
    if not await update:
        raise InvestigationOwnershipLostError("Investigation lease ownership was lost")


def _require_owned(
    state: InvestigationState | None,
    *,
    investigation_id: uuid.UUID,
    worker_id: str,
) -> InvestigationState:
    if state is None:
        raise PermanentInvestigationError(
            f"Investigation {investigation_id} does not exist",
            error_code="investigation_not_found",
        )
    if state.worker_id != worker_id:
        raise InvestigationOwnershipLostError("Investigation lease ownership was lost")
    return state


def _post_validate(
    analysis: ExceptionAnalysis,
    *,
    context: ControlContext,
    comment_limit: int,
) -> None:
    try:
        validate_codes(
            analysis,
            reason_codes=CodeVocabulary(
                name="ReasonCodes",
                codes=tuple(value.code for value in context.reason_codes),
            ),
            resolution_codes=CodeVocabulary(
                name="ResolutionCodes",
                codes=tuple(value.code for value in context.resolution_codes),
            ),
        )
    except ValueError as exc:
        raise InvalidAnalysisOutputError(str(exc)) from exc
    validate_comment_length(analysis, record_comment_limit=comment_limit)


def _permanent_error_code(error: Exception) -> str:
    if isinstance(error, ExceptionNotFoundError):
        return "exception_not_found"
    if isinstance(error, NoLinkedRecordsError):
        return "no_linked_records"
    return cast(str, getattr(error, "error_code", "invalid_control_configuration"))
