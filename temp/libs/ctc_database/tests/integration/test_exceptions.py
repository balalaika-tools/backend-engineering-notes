"""CTC exception fetch and schema discovery against PostgreSQL."""

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from ctc_database import (
    CtcExceptionRepository,
    ExceptionNotFoundError,
    ExceptionTableNames,
    NoLinkedRecordsError,
    build_engine,
)
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.requires_env("INTEGRATION_CTC_DATABASE_URL")]


def _database_url() -> str:
    value = os.environ.get("INTEGRATION_CTC_DATABASE_URL")
    if not value:
        pytest.skip("INTEGRATION_CTC_DATABASE_URL is required")
    return value


@dataclass(frozen=True)
class CtcTestDatabase:
    admin: AsyncEngine
    read_only: AsyncEngine
    schema: str

    def repository(self) -> CtcExceptionRepository:
        return CtcExceptionRepository(
            self.read_only,
            tables=ExceptionTableNames(schema=self.schema),
        )


@pytest_asyncio.fixture
async def ctc_database() -> AsyncIterator[CtcTestDatabase]:
    schema = f"ctc_test_{uuid.uuid4().hex}"
    url = make_url(_database_url())
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.execute(
            text(
                f'CREATE TABLE "{schema}".exceptions ('
                "externalid text NOT NULL, pk bytea PRIMARY KEY, version integer NOT NULL, "
                "business_value text, activestatus integer, lastupdated timestamptz)"
            )
        )
        await connection.execute(
            text(
                f'CREATE TABLE "{schema}".records ('
                "pk bytea PRIMARY KEY, system text, quantity numeric, latestcomment text)"
            )
        )
        await connection.execute(
            text(
                f'CREATE TABLE "{schema}".exceptionrecordlink ('
                "pk bytea PRIMARY KEY, recordpk bytea NOT NULL, exceptionpk bytea NOT NULL)"
            )
        )
    read_only = build_engine(
        database_url=url,
        pool_size=2,
    )
    yield CtcTestDatabase(admin=admin, read_only=read_only, schema=schema)
    await read_only.dispose()
    async with admin.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin.dispose()


async def _insert_exception(
    database: CtcTestDatabase,
    *,
    exception_id: str,
    exception_pk: bytes,
) -> None:
    async with database.admin.begin() as connection:
        await connection.execute(
            text(
                f'INSERT INTO "{database.schema}".exceptions '
                "(externalid, pk, version, business_value, activestatus) "
                "VALUES (:externalid, :pk, 7, 'visible', 1)"
            ),
            {"externalid": exception_id, "pk": exception_pk},
        )


@pytest.mark.asyncio
async def test_found_result_keeps_source_keys_and_strips_agent_metadata(
    ctc_database: CtcTestDatabase,
) -> None:
    exception_pk = b"exception-pk"
    record_pk = b"record-pk"
    await _insert_exception(
        ctc_database,
        exception_id="EX-1",
        exception_pk=exception_pk,
    )
    async with ctc_database.admin.begin() as connection:
        await connection.execute(
            text(
                f'INSERT INTO "{ctc_database.schema}".records '
                "(pk, system, quantity, latestcomment) VALUES (:pk, 'Internal', 12.5, 'hidden')"
            ),
            {"pk": record_pk},
        )
        await connection.execute(
            text(
                f'INSERT INTO "{ctc_database.schema}".exceptionrecordlink '
                "(pk, recordpk, exceptionpk) VALUES (:pk, :recordpk, :exceptionpk)"
            ),
            {"pk": b"link-pk", "recordpk": record_pk, "exceptionpk": exception_pk},
        )

    result = await ctc_database.repository().fetch("EX-1")
    assert result.exception_pk == exception_pk
    assert result.exception_version == 7
    assert result.records[0].pk == record_pk
    assert result.exception_view == {"externalid": "EX-1", "business_value": "visible"}
    assert result.records[0].agent_view == {"system": "Internal", "quantity": 12.5}


@pytest.mark.asyncio
async def test_unknown_exception_raises_not_found(ctc_database: CtcTestDatabase) -> None:
    with pytest.raises(ExceptionNotFoundError):
        await ctc_database.repository().fetch("UNKNOWN")


@pytest.mark.asyncio
async def test_exception_without_links_raises_no_linked_records(
    ctc_database: CtcTestDatabase,
) -> None:
    await _insert_exception(
        ctc_database,
        exception_id="EX-EMPTY",
        exception_pk=b"exception-empty",
    )

    with pytest.raises(NoLinkedRecordsError):
        await ctc_database.repository().fetch("EX-EMPTY")
