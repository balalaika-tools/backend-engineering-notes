"""Configuration-generation table."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, SmallInteger, text
from sqlmodel import Field, SQLModel


class ConfigState(SQLModel, table=True):
    """Singleton generation counter used to invalidate worker context caches."""

    __tablename__ = "config_state"
    __table_args__ = (CheckConstraint("id = 1", name="singleton_id"),)

    id: int = Field(sa_column=Column(SmallInteger, primary_key=True))
    generation: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default="0"),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
