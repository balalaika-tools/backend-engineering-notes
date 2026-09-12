"""HTTP request and response contracts for investigations."""

import uuid
from datetime import datetime
from typing import Literal

from orchestrator.domain.investigation import ExternalInvestigationStatus
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr, model_validator


class InvestigateRequest(BaseModel):
    """Select explicit exception IDs or a filtered backlog, never both."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"exception_ids": ["37889927", "37889928"]},
                {"created_at_gte": "2026-09-01T00:00:00Z", "max_exceptions": 20},
                {},
            ]
        },
    )

    exception_ids: list[StrictStr] | None = None
    created_at_gte: AwareDatetime | None = None
    max_exceptions: int = Field(default=1, gt=0, strict=True)

    @model_validator(mode="after")
    def validate_selection_mode(self) -> "InvestigateRequest":
        fields = self.model_fields_set
        if "exception_ids" in fields and self.exception_ids is None:
            raise ValueError("exception_ids must be an array")
        if "exception_ids" in fields and fields.intersection({"created_at_gte", "max_exceptions"}):
            raise ValueError("exception_ids cannot be combined with filtered selection fields")
        return self

    @property
    def is_explicit(self) -> bool:
        return "exception_ids" in self.model_fields_set


class TriggerInvestigationItem(BaseModel):
    investigation_id: uuid.UUID
    exception_id: str
    status: ExternalInvestigationStatus
    created: bool


class InvestigateResponse(BaseModel):
    request_id: uuid.UUID
    selected_count: int
    status_url: str
    investigations: list[TriggerInvestigationItem]


class QueryStatusRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"exception_ids": ["37889927"]},
                {"investigation_ids": ["1dd01cb1-6a5b-4ad7-a6ef-ac1e103190ec"]},
            ]
        }
    )

    exception_ids: list[str] | None = None
    investigation_ids: list[uuid.UUID] | None = None


class InvestigationStatusResponseItem(BaseModel):
    investigation_id: uuid.UUID | None
    exception_id: str | None
    status: ExternalInvestigationStatus
    attempt_count: int | None
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    last_error_code: str | None


class QueryStatusResponse(BaseModel):
    investigations: list[InvestigationStatusResponseItem]


class InvestigationRequestStatusSummaryResponse(BaseModel):
    total: int
    in_progress: int
    completed: int
    failed: int


class InvestigationRequestStatusResponse(BaseModel):
    request_id: uuid.UUID
    status: Literal["in_progress", "completed", "failed"]
    summary: InvestigationRequestStatusSummaryResponse
    investigations: list[InvestigationStatusResponseItem]


class FetchReportsRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"exception_ids": ["37889927"]},
                {"investigation_ids": ["1dd01cb1-6a5b-4ad7-a6ef-ac1e103190ec"]},
            ]
        }
    )

    exception_ids: list[str] | None = None
    investigation_ids: list[uuid.UUID] | None = None


class WriteBackResponse(BaseModel):
    comment: str
    codes: str
    detail: str | None


class StructuredAnalysisResponse(BaseModel):
    reason_code: str
    resolution_code: str
    confidence: Literal["high", "medium", "low"]
    short_analysis: str
    explanation: str
    reasoning: str
    write_back: WriteBackResponse


class InvestigationReportResponseItem(BaseModel):
    investigation_id: uuid.UUID | None
    exception_id: str | None
    status: ExternalInvestigationStatus
    completed_at: datetime | None
    last_error_code: str | None
    analysis: StructuredAnalysisResponse | None
    report: str | None
    report_uri: str | None


class FetchReportsResponse(BaseModel):
    reports: list[InvestigationReportResponseItem]
