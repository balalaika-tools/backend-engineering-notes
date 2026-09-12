"""Investigation state and checkpoint table."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field, SQLModel


class InvestigationStatus(StrEnum):
    """Internal durable investigation states."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


STATUS_ENUM = Enum(
    InvestigationStatus,
    name="investigation_status",
    values_callable=lambda members: [member.value for member in members],
)
ACTIVE_STATUS_PREDICATE = text("status IN ('queued', 'processing')")


class Investigation(SQLModel, table=True):
    """Authoritative lifecycle state for one run against an exception."""

    __tablename__ = "investigations"
    __table_args__ = (
        UniqueConstraint("event_id"),
        Index("investigations_exception_id_idx", "exception_id", desc("created_at")),
        Index(
            "investigations_status_idx",
            "status",
            postgresql_where=ACTIVE_STATUS_PREDICATE,
        ),
        Index("investigations_request_idx", "request_id"),
        Index(
            "investigations_retention_idx",
            "completed_at",
            postgresql_where=text("status IN ('completed', 'failed')"),
        ),
        Index(
            "investigations_active_uniq",
            "exception_id",
            unique=True,
            postgresql_where=ACTIVE_STATUS_PREDICATE,
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    request_id: uuid.UUID = Field(
        sa_column=Column(
            UUID(as_uuid=True),
            ForeignKey("api_requests.id"),
            nullable=False,
        )
    )
    client_id: str = Field(sa_column=Column(String, nullable=False))
    exception_id: str = Field(sa_column=Column(String, nullable=False))
    event_id: uuid.UUID = Field(sa_column=Column(UUID(as_uuid=True), nullable=False))
    status: InvestigationStatus = Field(
        default=InvestigationStatus.QUEUED,
        sa_column=Column(
            STATUS_ENUM,
            nullable=False,
            server_default=InvestigationStatus.QUEUED.value,
        ),
    )
    attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    worker_id: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_heartbeat_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_error_code: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    last_error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    report: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    report_uri: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    analysis: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB().with_variant(JSON(), "sqlite"), nullable=True),
    )
    analysis_persisted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    comment_outcome: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    comment_written_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    comment_detail: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    codes_outcome: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    codes_written_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    codes_detail: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
