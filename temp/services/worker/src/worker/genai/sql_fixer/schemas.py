"""Structured contracts for the SQL repair sub-agent."""

from pydantic import BaseModel, ConfigDict, Field


class SQLQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_sql: str = Field(min_length=1)
    result: str = Field(min_length=1)
