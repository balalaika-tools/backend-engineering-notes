"""Deterministic CTC exception fetch via primary-key joins."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ctc_database.models import (
    ExceptionData,
    ExceptionNotFoundError,
    ExceptionSourceUnavailableError,
    LinkedRecord,
    NoLinkedRecordsError,
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLATFORM_METADATA = frozenset(
    {
        "pk",
        "ultimateparentpk",
        "lastupdated",
        "raised",
        "lastactiondate",
        "lastactionby",
        "lastactiontype",
        "lastactionid",
        "activestatus",
        "assignedto",
        "version",
        "donotpurgebefore",
        "transactionstatus",
        "allowpurge",
        "hascomments",
        "latestcomment",
        "businesskey",
        "exceptionstatus",
        "exceptionstyle",
        "reasoncode",
        "resolutioncode",
        "raisedzoneid",
        "resolvedzoneid",
        "closedzoneid",
    }
)


@dataclass(frozen=True, slots=True)
class ExceptionTableNames:
    schema: str
    exceptions: str = "exceptions"
    links: str = "exceptionrecordlink"
    records: str = "records"


class CtcExceptionRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        tables: ExceptionTableNames | None = None,
    ) -> None:
        self._engine = engine
        self._default_tables = tables

    async def fetch(
        self,
        exception_id: str,
        *,
        rec_schema: str | None = None,
    ) -> ExceptionData:
        tables = self._table_names(rec_schema)
        exceptions = _qualified(tables.schema, tables.exceptions)
        links = _qualified(tables.schema, tables.links)
        records_table = _qualified(tables.schema, tables.records)
        try:
            async with self._engine.connect() as connection:
                exception = (
                    (
                        await connection.execute(
                            text(f"SELECT * FROM {exceptions} WHERE externalid = :id"),
                            {"id": exception_id},
                        )
                    )
                    .mappings()
                    .first()
                )
                if exception is None:
                    raise ExceptionNotFoundError(exception_id)
                records = (
                    (
                        await connection.execute(
                            text(
                                f"SELECT record.* FROM {records_table} AS record "
                                f"JOIN {links} AS link ON link.recordpk = record.pk "
                                "WHERE link.exceptionpk = :pk"
                            ),
                            {"pk": exception["pk"]},
                        )
                    )
                    .mappings()
                    .all()
                )
        except (ExceptionNotFoundError, NoLinkedRecordsError):
            raise
        except (OSError, SQLAlchemyError) as exc:
            raise ExceptionSourceUnavailableError(
                f"Failed to fetch CTC exception {exception_id!r}"
            ) from exc
        if not records:
            raise NoLinkedRecordsError(exception_id)
        return _data(exception, records)

    def _table_names(self, rec_schema: str | None) -> ExceptionTableNames:
        if rec_schema is not None:
            return ExceptionTableNames(schema=rec_schema)
        if self._default_tables is not None:
            return self._default_tables
        raise ValueError("A runtime CTC reconciliation schema is required")


def _data(exception: RowMapping, records: Sequence[RowMapping]) -> ExceptionData:
    exception_pk = _bytes_value(exception["pk"], name="exception pk")
    version = exception["version"]
    if not isinstance(version, int):
        raise ExceptionSourceUnavailableError("CTC exception version is not an integer")
    return ExceptionData(
        exception_pk=exception_pk,
        exception_version=version,
        exception_view=_strip_metadata(exception),
        records=tuple(
            LinkedRecord(
                pk=_bytes_value(record["pk"], name="record pk"),
                agent_view=_strip_metadata(record),
            )
            for record in records
        ),
    )


def _strip_metadata(row: RowMapping) -> dict[str, object]:
    return {
        key: value
        for key, value in row.items()
        if key not in _PLATFORM_METADATA and not isinstance(value, (bytes, memoryview))
    }


def _bytes_value(value: object, *, name: str) -> bytes:
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytes):
        return value
    raise ExceptionSourceUnavailableError(f"CTC {name} is not binary")


def _qualified(schema: str, table: str) -> str:
    if not _IDENTIFIER.fullmatch(schema) or not _IDENTIFIER.fullmatch(table):
        raise ValueError("CTC schema and table names must be simple SQL identifiers")
    return f'"{schema}"."{table}"'
