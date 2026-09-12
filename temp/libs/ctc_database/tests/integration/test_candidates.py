"""Candidate predicates, total-order pagination, and database failure safeguards."""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from ctc_database import CtcCandidateReader, ExceptionSourceUnavailableError, build_engine
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = [pytest.mark.integration, pytest.mark.requires_env("INTEGRATION_CTC_DATABASE_URL")]


@pytest_asyncio.fixture
async def candidate_database() -> AsyncIterator[tuple[AsyncEngine, str]]:
    engine = build_engine(
        database_url=make_url(os.environ["INTEGRATION_CTC_DATABASE_URL"]),
        pool_size=2,
        acquisition_timeout_seconds=0.05,
    )
    schema = f"ctc_candidates_{uuid.uuid4().hex}"
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await conn.execute(
            text(
                f'CREATE TABLE "{schema}".exceptions ('
                "pk bytea PRIMARY KEY, externalid text, raised timestamptz NOT NULL, "
                "resolved timestamptz, closed timestamptz, name text, exceptionstatus integer)"
            )
        )
        await conn.execute(
            text(f'CREATE TABLE "{schema}".exceptionrecordlink (exceptionpk bytea, recordpk bytea)')
        )
        for number, external_id, day, resolved, closed, name, linked in [
            (1, "BEFORE", 1, False, False, "Positions", True),
            (2, "A", 2, False, False, "Positions", True),
            (3, "A", 2, False, False, "Positions", True),
            (4, "B", 2, False, False, "Positions", True),
            (5, "RESOLVED", 2, True, False, "Positions", True),
            (6, "CLOSED", 2, False, True, "Positions", True),
            (7, "OTHER", 2, False, False, "Other", True),
            (8, None, 2, False, False, "Positions", True),
            (9, "UNLINKED", 2, False, False, "Positions", False),
        ]:
            created = datetime(2026, 9, day, tzinfo=UTC)
            await conn.execute(
                text(
                    f'INSERT INTO "{schema}".exceptions VALUES '
                    "(:pk, :id, :created, :resolved, :closed, :name, 17)"
                ),
                dict(
                    pk=bytes([number]),
                    id=external_id,
                    created=created,
                    resolved=created if resolved else None,
                    closed=created if closed else None,
                    name=name,
                ),
            )
            if linked:
                await conn.execute(
                    text(
                        f'INSERT INTO "{schema}".exceptionrecordlink VALUES (:pk, :pk), (:pk, :pk)'
                    ),
                    {"pk": bytes([number])},
                )
    try:
        yield engine, schema
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


@pytest.mark.asyncio
async def test_all_predicates_and_complete_cursor_prevent_skips_and_join_duplicates(
    candidate_database: tuple[AsyncEngine, str],
) -> None:
    engine, schema = candidate_database
    reader = CtcCandidateReader(engine, statement_timeout_seconds=1)
    after = None
    found = []
    for _ in range(5):
        page = await reader.read_page(
            schema=schema,
            exception_name="Positions",
            limit=1,
            created_at_gte=datetime(2026, 9, 2, tzinfo=UTC),
            after=after,
        )
        if not page:
            break
        found.extend(page)
        after = page[-1]
    assert [(row.external_id, row.pk) for row in found] == [
        ("A", b"\x02"),
        ("A", b"\x03"),
        ("B", b"\x04"),
    ]
    unbounded = await reader.read_page(schema=schema, exception_name="Positions", limit=10)
    assert [row.external_id for row in unbounded] == ["BEFORE", "A", "A", "B"]


@pytest.mark.asyncio
async def test_statement_timeout_is_translated_and_connection_remains_usable(
    candidate_database: tuple[AsyncEngine, str],
) -> None:
    engine, schema = candidate_database
    reader = CtcCandidateReader(engine, statement_timeout_seconds=0.02)
    async with engine.begin() as lock:
        await lock.execute(text(f'LOCK TABLE "{schema}".exceptions IN ACCESS EXCLUSIVE MODE'))
        with pytest.raises(ExceptionSourceUnavailableError):
            await reader.read_page(schema=schema, exception_name="Positions", limit=1)
    assert await reader.read_page(schema=schema, exception_name="Positions", limit=1)


@pytest.mark.asyncio
async def test_pool_acquisition_timeout_is_translated(
    candidate_database: tuple[AsyncEngine, str],
) -> None:
    engine, schema = candidate_database
    reader = CtcCandidateReader(engine, statement_timeout_seconds=1)
    async with engine.connect(), engine.connect():
        with pytest.raises(ExceptionSourceUnavailableError):
            await reader.read_page(schema=schema, exception_name="Positions", limit=1)


@pytest.mark.asyncio
async def test_unreachable_database_is_translated() -> None:
    engine = build_engine(
        database_url=make_url("postgresql+asyncpg://test@127.0.0.1:1/test"),
        pool_size=1,
    )
    try:
        with pytest.raises(ExceptionSourceUnavailableError):
            await CtcCandidateReader(engine, statement_timeout_seconds=1).read_page(
                schema="positions",
                exception_name="Positions",
                limit=1,
            )
    finally:
        await engine.dispose()
