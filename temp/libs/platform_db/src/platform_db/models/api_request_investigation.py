"""Stable membership of investigations in accepted API requests."""

import uuid

from sqlalchemy import Column, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlmodel import Field, SQLModel


class ApiRequestInvestigation(SQLModel, table=True):
    """Ordered association recorded for both created and attached investigations."""

    __tablename__ = "api_request_investigations"
    __table_args__ = (
        UniqueConstraint("request_id", "position"),
        Index("api_request_investigations_investigation_idx", "investigation_id"),
    )

    request_id: uuid.UUID = Field(
        sa_column=Column(
            UUID(as_uuid=True),
            ForeignKey("api_requests.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    investigation_id: uuid.UUID = Field(
        sa_column=Column(
            UUID(as_uuid=True),
            ForeignKey("investigations.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    position: int = Field(sa_column=Column(Integer, nullable=False))
