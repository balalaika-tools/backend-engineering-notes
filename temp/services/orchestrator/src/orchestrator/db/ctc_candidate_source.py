"""Translate shared CTC reads into the orchestrator-owned candidate contract."""

import ctc_database
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from orchestrator.ports.exception_candidates import (
    CandidateCursor,
    CandidateSourceUnavailableError,
    FilteredSelection,
)


class CtcCandidateSource:
    def __init__(self, reader: ctc_database.CtcCandidateReader) -> None:
        self._reader = reader

    async def read_page(
        self, *, selection: FilteredSelection, limit: int, after: CandidateCursor | None
    ) -> tuple[CandidateCursor, ...]:
        cursor = (
            ctc_database.CandidateCursor(after.created_at, after.external_id, after.pk)
            if after
            else None
        )
        with trace.get_tracer(__name__).start_as_current_span(
            "read CTC candidate page",
            record_exception=False,
            set_status_on_exception=False,
            attributes={"db.system.name": "postgresql", "app.page.limit": limit},
        ) as span:
            try:
                rows = await self._reader.read_page(
                    schema=selection.schema,
                    exception_name=selection.exception_name,
                    created_at_gte=selection.created_at_gte,
                    limit=limit,
                    after=cursor,
                )
            except ctc_database.ExceptionSourceUnavailableError as exc:
                span.set_status(StatusCode.ERROR)
                span.set_attribute("error.type", "ctc_database_unavailable")
                raise CandidateSourceUnavailableError(
                    "CTC candidate selection unavailable"
                ) from exc
            span.set_attribute("app.page.rows", len(rows))
        return tuple(CandidateCursor(row.created_at, row.external_id, row.pk) for row in rows)
