"""Investigation lifecycle inputs consumed by the worker."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class InvestigationExecutionError(RuntimeError):
    """Base failure contract consumed by the delivery handler."""

    def __init__(self, message: str, *, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class TransientInvestigationError(InvestigationExecutionError):
    """The investigation may succeed on a later delivery attempt."""


class PermanentInvestigationError(InvestigationExecutionError):
    """Retrying the same investigation cannot correct this failure."""


class InvestigationOwnershipLostError(RuntimeError):
    """A conditional checkpoint failed because another worker owns the lease."""


@dataclass(frozen=True, slots=True)
class InvestigationRequested:
    event_id: uuid.UUID
    event_type: str
    occurred_at: datetime
    investigation_id: uuid.UUID
    request_id: uuid.UUID
    client_id: str
    exception_id: str
    traceparent: str | None


class InvestigationStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class InvestigationState:
    id: uuid.UUID
    request_id: uuid.UUID
    exception_id: str
    status: InvestigationStatus
    attempt_count: int
    worker_id: str | None
    lease_expires_at: datetime | None
    last_heartbeat_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    last_error_code: str | None
    analysis: Mapping[str, object] | None
    analysis_persisted_at: datetime | None
    report: str | None
    report_uri: str | None
    comment_outcome: str | None
    comment_detail: str | None
    codes_outcome: str | None
    codes_detail: str | None
