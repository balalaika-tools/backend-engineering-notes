"""API request-log table."""

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Column, DateTime, Index, Integer, String, func
from sqlmodel import Field, SQLModel


class ApiRequest(SQLModel, table=True):
    """Audit record grouping investigations accepted in one API request."""

    __tablename__ = "api_requests"
    __table_args__ = (Index("api_requests_created_at_idx", "created_at"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    client_id: str = Field(sa_column=Column(String, nullable=False))
    exception_ids: list[str] = Field(sa_column=Column(ARRAY(String), nullable=False))
    request_kind: str = Field(
        default="explicit", sa_column=Column(String, nullable=False, server_default="explicit")
    )
    selection_created_at_gte: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    selection_max_exceptions: int | None = Field(default=None, sa_column=Column(Integer))
    selection_schema: str | None = Field(default=None, sa_column=Column(String))
    selection_exception_name: str | None = Field(default=None, sa_column=Column(String))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
