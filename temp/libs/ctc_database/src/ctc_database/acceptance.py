"""Redacted read-only checks of the deployed CTC candidate-selection contract."""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ctc_database.candidates import CtcCandidateReader
from ctc_database.exceptions import _qualified
from ctc_database.models import ExceptionSourceUnavailableError


class CtcAcceptanceError(RuntimeError):
    """The database does not satisfy a named acceptance check."""


@dataclass(frozen=True, slots=True)
class CtcAcceptanceReport:
    schema: str
    exception_name: str
    eligible_sample_count: int
    status_relationships: tuple[dict[str, int | bool | None], ...]


async def verify_candidate_contract(
    engine: AsyncEngine, *, schema: str, exception_name: str, statement_timeout_seconds: float
) -> CtcAcceptanceReport:
    exceptions = _qualified(schema, "exceptions")
    links = _qualified(schema, "exceptionrecordlink")
    records = _qualified(schema, "records")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": str(max(1, int(statement_timeout_seconds * 1000)))},
            )
            columns = (
                await connection.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = :schema AND table_name = 'exceptions'"
                    ),
                    {"schema": schema},
                )
            ).all()
            types = {str(row[0]): str(row[1]) for row in columns}
            required = {
                "pk",
                "raised",
                "resolved",
                "closed",
                "externalid",
                "name",
                "exceptionstatus",
            }
            if not required <= types.keys() or types.get("exceptionstatus", "") not in {
                "integer",
                "smallint",
                "bigint",
            }:
                raise CtcAcceptanceError("required_columns")
            writable = (
                await connection.execute(
                    text(
                        "SELECT has_table_privilege(current_user, :table, 'INSERT,UPDATE,DELETE,TRUNCATE')"
                    ),
                    {"table": exceptions},
                )
            ).scalar_one()
            if writable:
                raise CtcAcceptanceError("read_only_role")
            counts = (
                await connection.execute(
                    text(
                        f"SELECT count(*) AS named, count(*) FILTER (WHERE EXISTS ("
                        f"SELECT 1 FROM {links} AS l JOIN {records} AS r ON r.pk = l.recordpk "
                        "WHERE l.exceptionpk = e.pk)) AS linked "
                        f"FROM {exceptions} AS e WHERE e.name = :name"
                    ),
                    {"name": exception_name},
                )
            ).one()
            if counts.named == 0:
                raise CtcAcceptanceError("configured_exception_name")
            if counts.linked == 0:
                raise CtcAcceptanceError("linked_records")
            statuses = (
                (
                    await connection.execute(
                        text(
                            "SELECT exceptionstatus AS status, resolved IS NOT NULL AS resolved, "
                            "closed IS NOT NULL AS closed, count(*) AS count "
                            f"FROM {exceptions} WHERE name = :name "
                            "GROUP BY exceptionstatus, resolved IS NOT NULL, closed IS NOT NULL "
                            "ORDER BY exceptionstatus, resolved IS NOT NULL, closed IS NOT NULL LIMIT 101"
                        ),
                        {"name": exception_name},
                    )
                )
                .mappings()
                .all()
            )
            if len(statuses) > 100:
                raise CtcAcceptanceError("status_cardinality")
    except (OSError, SQLAlchemyError, TimeoutError) as exc:
        raise ExceptionSourceUnavailableError("CTC acceptance unavailable") from exc
    reader = CtcCandidateReader(engine, statement_timeout_seconds=statement_timeout_seconds)
    first = await reader.read_page(schema=schema, exception_name=exception_name, limit=2)
    second = (
        await reader.read_page(
            schema=schema, exception_name=exception_name, limit=2, after=first[-1]
        )
        if first
        else ()
    )
    keys = [(row.created_at, row.external_id, row.pk) for row in (*first, *second)]
    if keys != sorted(set(keys)):
        raise CtcAcceptanceError("bounded_ordering")
    return CtcAcceptanceReport(
        schema, exception_name, len(keys), tuple(dict(row) for row in statuses)
    )
