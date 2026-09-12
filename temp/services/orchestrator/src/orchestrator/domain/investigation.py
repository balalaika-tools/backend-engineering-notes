"""Investigation lifecycle vocabulary exposed to orchestrator actions."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class InvestigationStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ExternalInvestigationStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_FOUND = "not_found"


ACTIVE_STATUSES = frozenset({InvestigationStatus.QUEUED, InvestigationStatus.PROCESSING})


@dataclass(frozen=True, slots=True)
class InvestigationRecord:
    id: uuid.UUID
    request_id: uuid.UUID
    client_id: str
    exception_id: str
    event_id: uuid.UUID
    status: InvestigationStatus
    attempt_count: int
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class InvestigationInsertResult:
    investigation: InvestigationRecord
    created: bool


@dataclass(frozen=True, slots=True)
class InvestigationReportRecord:
    id: uuid.UUID
    exception_id: str
    status: InvestigationStatus
    completed_at: datetime | None
    last_error_code: str | None
    analysis: Mapping[str, object] | None
    report: str | None
    report_uri: str | None
    comment_outcome: str | None
    comment_detail: str | None
    codes_outcome: str | None
    codes_detail: str | None


def external_status(status: InvestigationStatus) -> ExternalInvestigationStatus:
    if status in ACTIVE_STATUSES:
        return ExternalInvestigationStatus.IN_PROGRESS
    return ExternalInvestigationStatus(status.value)
