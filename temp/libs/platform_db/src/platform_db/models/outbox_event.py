"""Transactional outbox table."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Column, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field, SQLModel


class OutboxEvent(SQLModel, table=True):
    """Publish intent committed atomically with an investigation."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index(
            "outbox_pending_idx",
            "next_attempt_at",
            postgresql_where=text("published_at IS NULL"),
        ),
        Index(
            "outbox_retention_idx",
            "published_at",
            postgresql_where=text("published_at IS NOT NULL"),
        ),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    event_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(UUID(as_uuid=True), nullable=False, unique=True),
    )
    aggregate_type: str = Field(sa_column=Column(String, nullable=False))
    aggregate_id: uuid.UUID = Field(sa_column=Column(UUID(as_uuid=True), nullable=False))
    event_type: str = Field(sa_column=Column(String, nullable=False))
    subject: str = Field(sa_column=Column(String, nullable=False))
    payload: dict[str, Any] = Field(
        sa_column=Column(JSONB().with_variant(JSON(), "sqlite"), nullable=False)
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    published_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    next_attempt_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    last_error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
