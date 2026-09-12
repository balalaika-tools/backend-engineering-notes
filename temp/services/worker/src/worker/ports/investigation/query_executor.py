"""Bounded read-query capability exposed to investigation tools."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class QueryRejectedError(ValueError):
    """A statement is not an allowlisted read query."""


class QueryExecutionError(RuntimeError):
    """The CTC database could not execute an allowlisted query."""


class QueryUnavailableError(QueryExecutionError):
    """The CTC database dependency is temporarily unavailable."""


@dataclass(frozen=True, slots=True)
class QueryResult:
    rows: tuple[Mapping[str, object], ...]
    truncated: bool
    row_limit: int

    def render(self) -> str:
        if not self.rows:
            return "Query executed successfully. No rows returned."
        output = json.dumps(self.rows, ensure_ascii=False, indent=2, default=str)
        if self.truncated:
            output += (
                f"\n\nWARNING: More rows were available; only the first "
                f"{self.row_limit} are shown to avoid flooding the context."
            )
        return output


class QueryExecutor(Protocol):
    async def execute(self, query: str) -> QueryResult: ...
