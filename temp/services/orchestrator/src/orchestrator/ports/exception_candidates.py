"""Caller-owned contracts for read-only filtered exception selection."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class CandidateSourceUnavailableError(RuntimeError):
    error_code = "ctc_database_unavailable"


@dataclass(frozen=True, slots=True)
class CandidateCursor:
    created_at: datetime
    external_id: str
    pk: bytes


@dataclass(frozen=True, slots=True)
class FilteredSelection:
    created_at_gte: datetime | None
    max_exceptions: int
    schema: str
    exception_name: str


class ExceptionCandidateSource(Protocol):
    async def read_page(
        self, *, selection: FilteredSelection, limit: int, after: CandidateCursor | None
    ) -> tuple[CandidateCursor, ...]: ...
