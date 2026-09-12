"""Report retrieval behavior at the application boundary."""

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import pytest
from orchestrator.application.fetch_reports import FetchReports, InvalidReportQueryError
from orchestrator.domain.investigation import (
    ExternalInvestigationStatus,
    InvestigationReportRecord,
    InvestigationStatus,
)

INVESTIGATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
NEWER_INVESTIGATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _completed_record() -> InvestigationReportRecord:
    return InvestigationReportRecord(
        id=INVESTIGATION_ID,
        exception_id="EX-1",
        status=InvestigationStatus.COMPLETED,
        completed_at=datetime.now(UTC),
        last_error_code=None,
        analysis={
            "reason_code": "CorporateActionRepair",
            "resolution_code": "Custodian",
            "confidence": "high",
            "short_analysis": "Matched the reference event.",
            "explanation": "The quantities and dates agree.",
            "reasoning": "Evidence points to a custodian repair.",
        },
        report="# Report",
        report_uri="s3://reports/2026/09/07/report.md",
        comment_outcome="written",
        comment_detail=None,
        codes_outcome="skipped_version_conflict",
        codes_detail="version conflict",
    )


@dataclass
class FakeReader:
    records: tuple[InvestigationReportRecord, ...]

    async def reports_by_ids(
        self, investigation_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, InvestigationReportRecord]:
        requested = set(investigation_ids)
        return {record.id: record for record in self.records if record.id in requested}

    async def most_recent_reports_by_exception_ids(
        self, exception_ids: list[str]
    ) -> dict[str, InvestigationReportRecord]:
        requested = set(exception_ids)
        return {
            record.exception_id: record
            for record in self.records
            if record.exception_id in requested
        }


@dataclass
class FakeUnitOfWork:
    investigations: FakeReader

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def _action(*records: InvestigationReportRecord, inline_limit: int = 256_000) -> FetchReports:
    return FetchReports(
        unit_of_work_factory=lambda: FakeUnitOfWork(FakeReader(records)),
        max_batch_size=100,
        inline_limit_bytes=inline_limit,
    )


@pytest.mark.asyncio
async def test_completed_report_exposes_structured_analysis_and_write_back() -> None:
    result = await _action(_completed_record()).execute(
        exception_ids=None,
        investigation_ids=[INVESTIGATION_ID],
    )

    item = result.reports[0]
    assert item.status is ExternalInvestigationStatus.COMPLETED
    assert item.analysis is not None
    assert item.analysis.reason_code == "CorporateActionRepair"
    assert item.analysis.resolution_code == "Custodian"
    assert item.analysis.write_back.comment == "written"
    assert item.analysis.write_back.codes == "skipped_version_conflict"
    assert item.report == "# Report"
    assert item.report_uri == "s3://reports/2026/09/07/report.md"


@pytest.mark.asyncio
async def test_exception_id_returns_its_latest_investigation() -> None:
    older = _completed_record()
    newer = replace(
        older,
        id=NEWER_INVESTIGATION_ID,
        status=InvestigationStatus.PROCESSING,
        completed_at=None,
    )

    result = await _action(older, newer).execute(
        exception_ids=[" EX-1 "],
        investigation_ids=None,
    )

    item = result.reports[0]
    assert item.investigation_id == NEWER_INVESTIGATION_ID
    assert item.status is ExternalInvestigationStatus.IN_PROGRESS
    assert item.analysis is None
    assert item.report is None


@pytest.mark.asyncio
async def test_exception_id_does_not_fall_back_after_latest_investigation_failed() -> None:
    older = _completed_record()
    newer = replace(
        older,
        id=NEWER_INVESTIGATION_ID,
        status=InvestigationStatus.FAILED,
        completed_at=None,
        last_error_code="analysis_unavailable",
    )

    result = await _action(older, newer).execute(
        exception_ids=["EX-1"],
        investigation_ids=None,
    )

    item = result.reports[0]
    assert item.investigation_id == NEWER_INVESTIGATION_ID
    assert item.status is ExternalInvestigationStatus.FAILED
    assert item.analysis is None
    assert item.report is None
    assert item.report_uri is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception_ids", "investigation_ids"),
    [
        (None, None),
        (["EX-1"], [INVESTIGATION_ID]),
    ],
)
async def test_query_requires_exactly_one_identifier_kind(
    exception_ids: list[str] | None,
    investigation_ids: list[uuid.UUID] | None,
) -> None:
    with pytest.raises(InvalidReportQueryError, match="exactly one"):
        await _action().execute(
            exception_ids=exception_ids,
            investigation_ids=investigation_ids,
        )


@pytest.mark.asyncio
async def test_failed_report_hides_analysis_and_includes_error_code() -> None:
    failed = replace(
        _completed_record(),
        status=InvestigationStatus.FAILED,
        last_error_code="exception_not_found",
    )

    result = await _action(failed).execute(
        exception_ids=None,
        investigation_ids=[INVESTIGATION_ID],
    )

    item = result.reports[0]
    assert item.status is ExternalInvestigationStatus.FAILED
    assert item.last_error_code == "exception_not_found"
    assert item.analysis is None
    assert item.report is None
    assert item.report_uri is None


@pytest.mark.asyncio
async def test_oversized_utf8_report_is_uri_only() -> None:
    oversized = replace(_completed_record(), report="ééé")

    result = await _action(oversized, inline_limit=5).execute(
        exception_ids=None,
        investigation_ids=[INVESTIGATION_ID],
    )

    assert result.reports[0].report is None
    assert result.reports[0].report_uri == oversized.report_uri


@pytest.mark.asyncio
async def test_malformed_analysis_is_isolated_to_its_report_item() -> None:
    malformed = replace(_completed_record(), analysis={"confidence": "certain"})
    healthy = replace(_completed_record(), id=NEWER_INVESTIGATION_ID, exception_id="EX-2")

    result = await _action(malformed, healthy).execute(
        exception_ids=None,
        investigation_ids=[INVESTIGATION_ID, NEWER_INVESTIGATION_ID],
    )

    assert result.reports[0].status is ExternalInvestigationStatus.COMPLETED
    assert result.reports[0].analysis is None
    assert result.reports[0].last_error_code == "invalid_persisted_analysis"
    assert result.reports[1].analysis is not None


@pytest.mark.asyncio
async def test_exception_id_lookup_returns_latest_investigation_without_fallback() -> None:
    older_completed = _completed_record()
    newer_failed = replace(
        older_completed,
        id=NEWER_INVESTIGATION_ID,
        status=InvestigationStatus.FAILED,
        completed_at=None,
        last_error_code="run_limit_exceeded",
    )

    result = await _action(older_completed, newer_failed).execute(
        exception_ids=[" EX-1 "],
        investigation_ids=None,
    )

    item = result.reports[0]
    assert item.investigation_id == NEWER_INVESTIGATION_ID
    assert item.status is ExternalInvestigationStatus.FAILED
    assert item.analysis is None
    assert item.report is None
    assert item.report_uri is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception_ids", "investigation_ids"),
    [
        (None, None),
        (["EX-1"], [INVESTIGATION_ID]),
        ([], None),
        (None, []),
    ],
)
async def test_report_query_requires_exactly_one_non_empty_selector(
    exception_ids: list[str] | None,
    investigation_ids: list[uuid.UUID] | None,
) -> None:
    with pytest.raises(InvalidReportQueryError):
        await _action().execute(
            exception_ids=exception_ids,
            investigation_ids=investigation_ids,
        )
