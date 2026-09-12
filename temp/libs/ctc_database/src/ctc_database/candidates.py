"""Bounded, read-only keyset queries for eligible CTC exceptions."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ctc_database.exceptions import _bytes_value, _qualified
from ctc_database.models import ExceptionSourceUnavailableError


@dataclass(frozen=True, slots=True)
class CandidateCursor:
    created_at: datetime
    external_id: str
    pk: bytes


class CtcCandidateReader:
    def __init__(self, engine: AsyncEngine, *, statement_timeout_seconds: float) -> None:
        if statement_timeout_seconds <= 0:
            raise ValueError("Statement timeout must be positive")
        self._engine = engine
        self._timeout_ms = max(1, int(statement_timeout_seconds * 1000))

    async def read_page(
        self,
        *,
        schema: str,
        exception_name: str,
        limit: int,
        created_at_gte: datetime | None = None,
        after: CandidateCursor | None = None,
    ) -> tuple[CandidateCursor, ...]:
        if limit <= 0:
            raise ValueError("Page size must be positive")
        exceptions = _qualified(schema, "exceptions")
        links = _qualified(schema, "exceptionrecordlink")
        predicates = [
            "e.resolved IS NULL",
            "e.closed IS NULL",
            "e.externalid IS NOT NULL",
            "e.name = :exception_name",
            f"EXISTS (SELECT 1 FROM {links} AS link WHERE link.exceptionpk = e.pk)",
        ]
        parameters: dict[str, object] = {"exception_name": exception_name, "limit": limit}
        if created_at_gte is not None:
            predicates.append("e.raised >= :created_at_gte")
            parameters["created_at_gte"] = created_at_gte
        if after is not None:
            predicates.append(
                "(e.raised, e.externalid, e.pk) > (:created_at, :external_id, :pk)"
            )
            parameters.update(
                created_at=after.created_at, external_id=after.external_id, pk=after.pk
            )
        query = (
            f"SELECT e.raised, e.externalid, e.pk FROM {exceptions} AS e WHERE "
            + " AND ".join(predicates)
            + " ORDER BY e.raised ASC, e.externalid ASC, e.pk ASC LIMIT :limit"
        )
        return await self._execute(query, parameters)

    async def _execute(
        self, query: str, parameters: dict[str, object]
    ) -> tuple[CandidateCursor, ...]:
        try:
            async with self._engine.begin() as connection:
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(
                    text("SELECT set_config('statement_timeout', :timeout, true)"),
                    {"timeout": str(self._timeout_ms)},
                )
                rows = (await connection.execute(text(query), parameters)).mappings().all()
        except (OSError, SQLAlchemyError, TimeoutError) as exc:
            raise ExceptionSourceUnavailableError("CTC candidate selection unavailable") from exc
        return tuple(
            CandidateCursor(
                created_at=row["raised"],
                external_id=row["externalid"],
                pk=_bytes_value(row["pk"], name="exception pk"),
            )
            for row in rows
        )
