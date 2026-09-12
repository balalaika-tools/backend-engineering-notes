"""Translate the shared CTC reader into the worker-owned exception-source contract."""

import ctc_database
from ctc_database import ExceptionTableNames
from sqlalchemy.ext.asyncio import AsyncEngine
from worker.ports.investigation.exception_source import (
    ExceptionData,
    ExceptionNotFoundError,
    ExceptionSourceUnavailableError,
    LinkedRecord,
    NoLinkedRecordsError,
)

__all__ = ["CtcExceptionRepository", "ExceptionTableNames"]


class CtcExceptionRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        tables: ExceptionTableNames | None = None,
    ) -> None:
        self._reader = ctc_database.CtcExceptionRepository(engine, tables=tables)

    async def fetch(self, exception_id: str, *, rec_schema: str | None = None) -> ExceptionData:
        try:
            result = await self._reader.fetch(exception_id, rec_schema=rec_schema)
        except ctc_database.ExceptionNotFoundError as exc:
            raise ExceptionNotFoundError(exception_id) from exc
        except ctc_database.NoLinkedRecordsError as exc:
            raise NoLinkedRecordsError(exception_id) from exc
        except ctc_database.ExceptionSourceUnavailableError as exc:
            raise ExceptionSourceUnavailableError(
                f"Failed to fetch CTC exception {exception_id!r}"
            ) from exc
        return ExceptionData(
            exception_pk=result.exception_pk,
            exception_version=result.exception_version,
            exception_view=result.exception_view,
            records=tuple(
                LinkedRecord(pk=row.pk, agent_view=row.agent_view) for row in result.records
            ),
        )
