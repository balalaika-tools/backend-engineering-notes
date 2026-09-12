"""Database-backed parity checks for the migrated platform schema."""

import os
from collections.abc import Sequence
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from platform_db import SQLModel
from platform_migrations.database_url import resolve_database_url
from sqlalchemy import URL, Connection, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_env("INTEGRATION_PLATFORM_DATABASE_URL"),
]


def _database_url() -> URL:
    value = os.environ.get("INTEGRATION_PLATFORM_DATABASE_URL")
    if not value:
        pytest.skip("INTEGRATION_PLATFORM_DATABASE_URL is required for database integration tests")
    return resolve_database_url({"INTEGRATION_PLATFORM_DATABASE_URL": value})


def _schema_differences(connection: Connection) -> Sequence[Any]:
    context = MigrationContext.configure(
        connection,
        opts={"compare_type": True, "compare_server_default": True},
    )
    return compare_metadata(context, SQLModel.metadata)


def _table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


@pytest.mark.asyncio
async def test_migrated_database_matches_models_and_has_config_singleton() -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            differences = await connection.run_sync(_schema_differences)
            table_names = await connection.run_sync(_table_names)
            seed = (await connection.execute(text("SELECT id, generation FROM config_state"))).one()
    finally:
        await engine.dispose()

    assert differences == []
    assert table_names == {
        "alembic_version",
        "api_request_investigations",
        "api_requests",
        "config_state",
        "investigations",
        "outbox_events",
    }
    assert seed.id == 1
    assert seed.generation >= 0
