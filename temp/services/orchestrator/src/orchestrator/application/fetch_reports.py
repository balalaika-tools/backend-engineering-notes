"""Fetch structured analysis and bounded inline reports for investigations."""

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from orchestrator.domain.investigation import (
    ExternalInvestigationStatus,
    InvestigationReportRecord,
    InvestigationStatus,
    external_status,
)
from orchestrator.ports.investigation_store import ReportQueryUnitOfWork

Confidence = Literal["high", "medium", "low"]


class InvalidReportQueryError(ValueError):
    """The query does not select exactly one non-empty identifier kind."""


class ReportBatchTooLargeError(ValueError):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Batch exceeds the configured limit of {limit}")


class InvalidPersistedAnalysisError(ValueError):
    """One stored analysis cannot be rendered without failing the whole batch."""


@dataclass(frozen=True, slots=True)
class WriteBackSummary:
    comment: str
    codes: str
    detail: str | None


@dataclass(frozen=True, slots=True)
class StructuredAnalysis:
    reason_code: str
    resolution_code: str
    confidence: Confidence
    short_analysis: str
    explanation: str
    reasoning: str
    write_back: WriteBackSummary


@dataclass(frozen=True, slots=True)
class InvestigationReportItem:
    investigation_id: uuid.UUID | None
    exception_id: str | None
    status: ExternalInvestigationStatus
    completed_at: datetime | None
    last_error_code: str | None
    analysis: StructuredAnalysis | None
    report: str | None
    report_uri: str | None


@dataclass(frozen=True, slots=True)
class InvestigationReportBatch:
    reports: tuple[InvestigationReportItem, ...]


class FetchReports:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], ReportQueryUnitOfWork],
        max_batch_size: int,
        inline_limit_bytes: int,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._max_batch_size = max_batch_size
        self._inline_limit_bytes = inline_limit_bytes

    async def execute(
        self,
        *,
        exception_ids: list[str] | None,
        investigation_ids: list[uuid.UUID] | None,
    ) -> InvestigationReportBatch:
        if (exception_ids is None) == (investigation_ids is None):
            raise InvalidReportQueryError(
                "Provide exactly one of exception_ids or investigation_ids"
            )
        if exception_ids is not None:
            values = self._validate_exception_ids(exception_ids)
            async with self._unit_of_work_factory() as unit_of_work:
                records_by_exception_id = (
                    await unit_of_work.investigations.most_recent_reports_by_exception_ids(values)
                )
            return InvestigationReportBatch(
                reports=tuple(
                    self._item(
                        records_by_exception_id.get(exception_id),
                        exception_id=exception_id,
                    )
                    for exception_id in values
                )
            )

        assert investigation_ids is not None
        self._validate_size(investigation_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            records_by_id = await unit_of_work.investigations.reports_by_ids(investigation_ids)
        return InvestigationReportBatch(
            reports=tuple(
                self._item(
                    records_by_id.get(investigation_id),
                    investigation_id=investigation_id,
                )
                for investigation_id in investigation_ids
            )
        )

    def _item(
        self,
        record: InvestigationReportRecord | None,
        *,
        investigation_id: uuid.UUID | None = None,
        exception_id: str | None = None,
    ) -> InvestigationReportItem:
        if record is None:
            return InvestigationReportItem(
                investigation_id=investigation_id,
                exception_id=exception_id,
                status=ExternalInvestigationStatus.NOT_FOUND,
                completed_at=None,
                last_error_code=None,
                analysis=None,
                report=None,
                report_uri=None,
            )
        if record.status is not InvestigationStatus.COMPLETED:
            return InvestigationReportItem(
                investigation_id=record.id,
                exception_id=record.exception_id,
                status=external_status(record.status),
                completed_at=record.completed_at,
                last_error_code=record.last_error_code,
                analysis=None,
                report=None,
                report_uri=None,
            )
        try:
            analysis = _structured_analysis(record)
        except InvalidPersistedAnalysisError:
            analysis = None
        return InvestigationReportItem(
            investigation_id=record.id,
            exception_id=record.exception_id,
            status=ExternalInvestigationStatus.COMPLETED,
            completed_at=record.completed_at,
            last_error_code=(
                record.last_error_code if analysis is not None else "invalid_persisted_analysis"
            ),
            analysis=analysis,
            report=self._inline_report(record.report),
            report_uri=record.report_uri,
        )

    def _validate_exception_ids(self, exception_ids: list[str]) -> list[str]:
        self._validate_size(exception_ids)
        normalized = [value.strip() for value in exception_ids]
        if any(not value for value in normalized):
            raise InvalidReportQueryError("Exception IDs must be non-empty strings")
        return normalized

    def _validate_size(self, values: Sequence[object]) -> None:
        if not values:
            raise InvalidReportQueryError("At least one identifier is required")
        if len(values) > self._max_batch_size:
            raise ReportBatchTooLargeError(self._max_batch_size)

    def _inline_report(self, report: str | None) -> str | None:
        if report is None or len(report.encode("utf-8")) > self._inline_limit_bytes:
            return None
        return report


def _structured_analysis(record: InvestigationReportRecord) -> StructuredAnalysis:
    if record.analysis is None:
        raise InvalidPersistedAnalysisError(
            f"Completed investigation {record.id} has no persisted analysis"
        )
    confidence_value = _required_string(record.analysis, "confidence")
    if confidence_value not in {"high", "medium", "low"}:
        raise InvalidPersistedAnalysisError(
            f"Investigation {record.id} has an invalid confidence value"
        )
    confidence = cast(Confidence, confidence_value)
    return StructuredAnalysis(
        reason_code=_required_string(record.analysis, "reason_code"),
        resolution_code=_required_string(record.analysis, "resolution_code"),
        confidence=confidence,
        short_analysis=_required_string(record.analysis, "short_analysis"),
        explanation=_required_string(record.analysis, "explanation"),
        reasoning=_required_string(record.analysis, "reasoning"),
        write_back=WriteBackSummary(
            comment=record.comment_outcome or "pending",
            codes=record.codes_outcome or "pending",
            detail=record.codes_detail or record.comment_detail,
        ),
    )


def _required_string(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidPersistedAnalysisError(
            f"Persisted analysis field {key!r} is missing or invalid"
        )
    return value
