"""Checkpointed investigation behavior and error classification."""

import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest
from worker.application.investigate_exception import InvestigateException, WriteBackSettings
from worker.application.resolve_control_context import VocabularyUnavailableError
from worker.domain.analysis import ExceptionAnalysis
from worker.domain.control_context import ControlContext, ControlManual, DecodeValue
from worker.domain.investigation import (
    InvestigationState,
    InvestigationStatus,
    PermanentInvestigationError,
    TransientInvestigationError,
)
from worker.ports.control_context.config_generation import ConfigGenerationUnavailableError
from worker.ports.control_context.config_summarizer import ConfigSummarizationFailedError
from worker.ports.investigation.ctc_write_back import (
    CtcWriteBackUnavailableError,
    WriteBackOutcome,
    WriteBackResult,
)
from worker.ports.investigation.exception_analyst import (
    AnalysisFailedError,
    AnalysisUnavailableError,
    ExceptionAnalysisInput,
)
from worker.ports.investigation.exception_source import (
    ExceptionData,
    ExceptionNotFoundError,
    LinkedRecord,
)
from worker.ports.investigation.investigation_store import InvestigationStoreUnavailableError

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
INVESTIGATION_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WORKER_ID = "worker-a"
ANALYSIS = ExceptionAnalysis(
    explanation="The custodian feed confirms the unmatched trade.",
    reasoning="The identifiers, amount, and date match.",
    reason_code="AccountBlocked",
    resolution_code="Custodian",
    short_analysis="The custodian must release the blocked account.",
    confidence="high",
)


def _state(*, persisted: bool = False) -> InvestigationState:
    return InvestigationState(
        id=INVESTIGATION_ID,
        request_id=uuid.uuid4(),
        exception_id="EX-1",
        status=InvestigationStatus.PROCESSING,
        attempt_count=1,
        worker_id=WORKER_ID,
        lease_expires_at=NOW,
        last_heartbeat_at=NOW,
        started_at=NOW,
        completed_at=None,
        last_error_code=None,
        analysis={
            "explanation": ANALYSIS.explanation,
            "reasoning": ANALYSIS.reasoning,
            "reason_code": ANALYSIS.reason_code,
            "resolution_code": ANALYSIS.resolution_code,
            "short_analysis": ANALYSIS.short_analysis,
            "confidence": ANALYSIS.confidence,
            "_exception_version": 3,
        }
        if persisted
        else None,
        analysis_persisted_at=NOW if persisted else None,
        report="# report" if persisted else None,
        report_uri="s3://reports/existing.md" if persisted else None,
        comment_outcome=None,
        comment_detail=None,
        codes_outcome=None,
        codes_detail=None,
    )


def _context() -> ControlContext:
    return ControlContext(
        generation=1,
        positions=ControlManual("Positions", "p-md5", "positions manual"),
        reference=ControlManual("Reference", "r-md5", "reference manual"),
        reason_codes=(DecodeValue("AccountBlocked", "Blocked", None),),
        resolution_codes=(DecodeValue("Custodian", "Custodian", None),),
        positions_rec_schema="webuiPositions",
        positions_tenant_schema="webui",
        reference_rec_schema="webuiReference",
        reference_tenant_schema="webui",
        reason_code_feature_id="reason-feature-id",
        resolution_code_feature_id="resolution-feature-id",
    )


class FakeRepository:
    def __init__(self, state: InvestigationState) -> None:
        self.state = state
        self.checkpoints: list[tuple[str, str, str | None]] = []
        self.persisted_analyses = 0

    async def get(self, investigation_id: uuid.UUID) -> InvestigationState | None:
        assert investigation_id == self.state.id
        return self.state

    async def persist_analysis(self, investigation_id: uuid.UUID, **values: object) -> bool:
        assert investigation_id == self.state.id
        self.persisted_analyses += 1
        self.state = replace(
            self.state,
            analysis=cast(Mapping[str, object], values["analysis"]),
            analysis_persisted_at=NOW,
            report=cast(str, values["report"]),
            report_uri=str(values["report_uri"]),
        )
        return True

    async def record_write_back_step(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        step: str,
        outcome: str,
        detail: str | None = None,
    ) -> bool:
        assert investigation_id == self.state.id
        assert worker_id == WORKER_ID
        self.checkpoints.append((step, outcome, detail))
        if step == "comment":
            self.state = replace(
                self.state,
                comment_outcome=outcome,
                comment_detail=detail,
            )
        else:
            self.state = replace(self.state, codes_outcome=outcome, codes_detail=detail)
        return True

    async def complete(self, investigation_id: uuid.UUID, *, worker_id: str) -> bool:
        assert investigation_id == self.state.id
        assert worker_id == WORKER_ID
        self.state = replace(self.state, status=InvestigationStatus.COMPLETED)
        return True


class FakeUnitOfWork:
    def __init__(self, repository: FakeRepository) -> None:
        self.investigations = repository
        self.commits = 0
        self.active = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        self.active = True
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.active = False
        return None

    async def commit(self) -> None:
        self.commits += 1


class FakeResolver:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def resolve(self) -> ControlContext:
        if self.error is not None:
            raise self.error
        return _context()


class FakeSource:
    def __init__(self, error: Exception | None = None, *, exception_version: int = 3) -> None:
        self.error = error
        self.exception_version = exception_version
        self.calls = 0

    async def fetch(self, exception_id: str, *, rec_schema: str) -> ExceptionData:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert exception_id == "EX-1"
        assert rec_schema == "webuiPositions"
        return ExceptionData(
            exception_pk=uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee").bytes,
            exception_version=self.exception_version,
            exception_view={"ExternalId": exception_id},
            records=(
                LinkedRecord(
                    pk=uuid.UUID("11111111-2222-3333-4444-555555555555").bytes,
                    agent_view={"Amount": "42"},
                ),
            ),
        )


class FakeAnalyst:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[ExceptionAnalysisInput] = []

    async def analyze(
        self,
        value: ExceptionAnalysisInput,
        *,
        context: ControlContext,
    ) -> ExceptionAnalysis:
        assert context == _context()
        self.calls.append(value)
        if self.error is not None:
            raise self.error
        return ANALYSIS


class FakeReportStore:
    def __init__(self) -> None:
        self.reports: list[str] = []

    async def store(self, **values: object) -> str:
        assert values["investigation_id"] == INVESTIGATION_ID
        self.reports.append(str(values["report"]))
        return "s3://reports/new.md"


class FakeWriteBack:
    def __init__(
        self,
        *,
        comment_result: WriteBackResult | None = None,
        codes_result: WriteBackResult | None = None,
        comment_error: Exception | None = None,
        codes_error: Exception | None = None,
    ) -> None:
        self.comment_result = comment_result or WriteBackResult(WriteBackOutcome.WRITTEN)
        self.codes_result = codes_result or WriteBackResult(WriteBackOutcome.WRITTEN)
        self.comment_error = comment_error
        self.codes_error = codes_error
        self.comment_calls = 0
        self.codes_calls = 0
        self.code_versions: list[int] = []
        self.call_order: list[str] = []

    async def write_comment(self, **values: object) -> WriteBackResult:
        self.comment_calls += 1
        self.call_order.append("comment")
        assert values["record_pks"]
        if self.comment_error is not None:
            raise self.comment_error
        return self.comment_result

    async def write_codes(self, **values: object) -> WriteBackResult:
        self.codes_calls += 1
        self.call_order.append("codes")
        self.code_versions.append(cast(int, values["exception_version"]))
        if self.codes_error is not None:
            raise self.codes_error
        assert values["reason_code_feature_id"] == "reason-feature-id"
        assert values["resolution_code_feature_id"] == "resolution-feature-id"
        assert values["reason_code"] == "AccountBlocked"
        assert values["resolution_code"] == "Custodian"
        return self.codes_result


def _action(
    repository: FakeRepository,
    *,
    resolver: FakeResolver | None = None,
    source: FakeSource | None = None,
    analyst: FakeAnalyst | None = None,
    write_back: FakeWriteBack | None = None,
    comment_enabled: bool = True,
    codes_enabled: bool = True,
) -> tuple[InvestigateException, FakeUnitOfWork, FakeAnalyst, FakeWriteBack]:
    unit_of_work = FakeUnitOfWork(repository)
    selected_analyst = analyst or FakeAnalyst()
    selected_write_back = write_back or FakeWriteBack()
    action = InvestigateException(
        unit_of_work_factory=lambda: unit_of_work,
        context_resolver=resolver or FakeResolver(),
        exception_source=source or FakeSource(),
        analyst=selected_analyst,
        report_store=FakeReportStore(),
        write_back=selected_write_back,
        write_back_settings=WriteBackSettings(
            tenant_token="WEBUI",
            control_name="Positions",
            exception_name="Break",
            record_comment_limit=200,
            comment_enabled=comment_enabled,
            codes_enabled=codes_enabled,
        ),
        report_inline_limit=256_000,
        clock=lambda: NOW,
    )
    return action, unit_of_work, selected_analyst, selected_write_back


@pytest.mark.asyncio
async def test_full_success_checkpoints_analysis_writes_and_completion() -> None:
    repository = FakeRepository(_state())
    action, unit_of_work, analyst, write_back = _action(repository)

    await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert repository.state.status is InvestigationStatus.COMPLETED
    assert repository.persisted_analyses == 1
    assert repository.checkpoints == [
        ("codes", "written", None),
        ("comment", "written", None),
    ]
    assert len(analyst.calls) == 1
    assert write_back.comment_calls == write_back.codes_calls == 1
    assert write_back.call_order == ["codes", "comment"]
    assert unit_of_work.commits == 4


@pytest.mark.asyncio
async def test_transient_analysis_failure_is_classified_for_retry() -> None:
    repository = FakeRepository(_state())
    action, _, _, _ = _action(
        repository,
        analyst=FakeAnalyst(AnalysisUnavailableError("provider timeout")),
    )

    with pytest.raises(TransientInvestigationError) as error:
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert error.value.error_code == "analysis_unavailable"
    assert repository.persisted_analyses == 0
    assert repository.checkpoints == []


@pytest.mark.asyncio
async def test_non_retryable_analysis_failure_is_classified_as_permanent() -> None:
    repository = FakeRepository(_state())
    action, _, _, _ = _action(
        repository,
        analyst=FakeAnalyst(AnalysisFailedError("invalid provider request")),
    )

    with pytest.raises(PermanentInvestigationError) as error:
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert error.value.error_code == "analysis_failed"
    assert repository.persisted_analyses == 0


@pytest.mark.asyncio
async def test_non_retryable_summarization_failure_is_classified_as_permanent() -> None:
    repository = FakeRepository(_state())
    action, _, analyst, _ = _action(
        repository,
        resolver=FakeResolver(ConfigSummarizationFailedError("invalid provider request")),
    )

    with pytest.raises(PermanentInvestigationError) as error:
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert error.value.error_code == "config_summarization_failed"
    assert analyst.calls == []


@pytest.mark.asyncio
async def test_generation_failure_is_transient_and_stops_before_context_dependent_work() -> None:
    repository = FakeRepository(_state())
    source = FakeSource()
    action, _, analyst, _ = _action(
        repository,
        resolver=FakeResolver(ConfigGenerationUnavailableError("database unavailable")),
        source=source,
    )

    with pytest.raises(TransientInvestigationError) as error:
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert error.value.error_code == "platform_database_unavailable"
    assert source.calls == 0
    assert analyst.calls == []


@pytest.mark.asyncio
async def test_persistence_failure_is_classified_for_retry() -> None:
    class UnavailableRepository(FakeRepository):
        async def get(self, investigation_id: uuid.UUID) -> InvestigationState | None:
            del investigation_id
            raise InvestigationStoreUnavailableError("database unavailable")

    action, _, _, _ = _action(UnavailableRepository(_state()))

    with pytest.raises(TransientInvestigationError) as error:
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert error.value.error_code == "platform_database_unavailable"


@pytest.mark.asyncio
async def test_unknown_exception_is_a_permanent_failure() -> None:
    repository = FakeRepository(_state())
    action, _, analyst, _ = _action(
        repository,
        source=FakeSource(ExceptionNotFoundError("EX-1")),
    )

    with pytest.raises(PermanentInvestigationError) as error:
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert error.value.error_code == "exception_not_found"
    assert analyst.calls == []


@pytest.mark.asyncio
async def test_missing_vocabulary_is_permanent_and_stops_before_fetch() -> None:
    repository = FakeRepository(_state())
    source = FakeSource()
    action, _, _, _ = _action(
        repository,
        resolver=FakeResolver(VocabularyUnavailableError("ResolutionCodes missing")),
        source=source,
    )

    with pytest.raises(PermanentInvestigationError) as error:
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert error.value.error_code == "vocabulary_unavailable"
    assert source.calls == 0


@pytest.mark.asyncio
async def test_retry_after_comment_failure_resumes_after_codes_checkpoint() -> None:
    repository = FakeRepository(_state(persisted=True))
    write_back = FakeWriteBack(comment_error=CtcWriteBackUnavailableError("timeout"))
    action, _, analyst, _ = _action(repository, write_back=write_back)

    with pytest.raises(TransientInvestigationError) as error:
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert error.value.error_code == "ctc_write_back_unavailable"
    assert analyst.calls == []
    assert repository.persisted_analyses == 0
    assert repository.checkpoints == [("codes", "written", None)]

    write_back.comment_error = None
    await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert repository.state.status is InvestigationStatus.COMPLETED
    assert write_back.codes_calls == 1
    assert write_back.comment_calls == 2
    assert repository.checkpoints == [
        ("codes", "written", None),
        ("comment", "written", None),
    ]


@pytest.mark.asyncio
async def test_retry_after_codes_failure_does_not_write_comment_early() -> None:
    repository = FakeRepository(_state(persisted=True))
    write_back = FakeWriteBack(codes_error=CtcWriteBackUnavailableError("timeout"))
    action, _, analyst, _ = _action(repository, write_back=write_back)

    with pytest.raises(TransientInvestigationError):
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert analyst.calls == []
    assert write_back.call_order == ["codes"]
    assert repository.checkpoints == []

    write_back.codes_error = None
    await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert write_back.call_order == ["codes", "codes", "comment"]
    assert repository.state.status is InvestigationStatus.COMPLETED


@pytest.mark.asyncio
async def test_codes_version_conflict_is_skipped_and_completes() -> None:
    repository = FakeRepository(_state())
    write_back = FakeWriteBack(
        codes_result=WriteBackResult(
            WriteBackOutcome.SKIPPED_VERSION_CONFLICT,
            "HTTP 409: version changed",
        )
    )
    action, _, _, _ = _action(repository, write_back=write_back)

    await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert repository.state.status is InvestigationStatus.COMPLETED
    assert repository.state.codes_outcome == "skipped_version_conflict"
    assert repository.state.codes_detail == "HTTP 409: version changed"


@pytest.mark.asyncio
async def test_platform_session_is_closed_before_external_context_resolution() -> None:
    repository = FakeRepository(_state())
    unit_of_work = FakeUnitOfWork(repository)

    class TrackingResolver(FakeResolver):
        async def resolve(self) -> ControlContext:
            assert unit_of_work.active is False
            return await super().resolve()

    action = InvestigateException(
        unit_of_work_factory=lambda: unit_of_work,
        context_resolver=TrackingResolver(),
        exception_source=FakeSource(),
        analyst=FakeAnalyst(),
        report_store=FakeReportStore(),
        write_back=FakeWriteBack(),
        write_back_settings=WriteBackSettings(
            tenant_token="WEBUI",
            control_name="Positions",
            exception_name="Break",
            record_comment_limit=200,
            comment_enabled=True,
            codes_enabled=True,
        ),
        report_inline_limit=256_000,
        clock=lambda: NOW,
    )

    await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)


@pytest.mark.asyncio
async def test_retry_uses_exception_version_captured_with_analysis() -> None:
    repository = FakeRepository(_state(persisted=True))
    write_back = FakeWriteBack()
    action, _, _, _ = _action(
        repository,
        source=FakeSource(exception_version=4),
        write_back=write_back,
    )

    await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert write_back.code_versions == [3]


@pytest.mark.asyncio
async def test_codes_rejection_is_checkpointed_before_permanent_failure() -> None:
    repository = FakeRepository(_state())
    write_back = FakeWriteBack(
        codes_result=WriteBackResult(WriteBackOutcome.FAILED, "HTTP 400: unknown feature")
    )
    action, unit_of_work, _, _ = _action(repository, write_back=write_back)

    with pytest.raises(PermanentInvestigationError) as error:
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert error.value.error_code == "write_back_rejected"
    assert repository.state.codes_outcome == "failed"
    assert unit_of_work.commits == 2


@pytest.mark.asyncio
async def test_comment_rejection_detail_is_checkpointed_before_permanent_failure() -> None:
    repository = FakeRepository(_state())
    write_back = FakeWriteBack(
        comment_result=WriteBackResult(WriteBackOutcome.FAILED, "HTTP 400: comments disabled")
    )
    action, _, _, _ = _action(repository, write_back=write_back)

    with pytest.raises(PermanentInvestigationError):
        await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert repository.state.comment_outcome == "failed"
    assert repository.state.comment_detail == "HTTP 400: comments disabled"


@pytest.mark.asyncio
async def test_disabled_steps_are_checkpointed_and_investigation_still_completes() -> None:
    repository = FakeRepository(_state())
    action, _, _, write_back = _action(
        repository,
        comment_enabled=False,
        codes_enabled=False,
    )

    await action.execute(investigation_id=INVESTIGATION_ID, worker_id=WORKER_ID)

    assert repository.state.status is InvestigationStatus.COMPLETED
    assert repository.checkpoints == [
        ("codes", "skipped_disabled", None),
        ("comment", "skipped_disabled", None),
    ]
    assert write_back.comment_calls == write_back.codes_calls == 0
