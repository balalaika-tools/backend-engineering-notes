"""Database-backed idempotency checks for the platform login bootstrap."""

import os
import uuid

import pytest
from platform_migrations.database_bootstrap import reconcile_platform_login
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_env("INTEGRATION_PLATFORM_DATABASE_URL"),
]


def _database_url() -> str:
    value = os.environ.get("INTEGRATION_PLATFORM_DATABASE_URL")
    if not value:
        pytest.skip("INTEGRATION_PLATFORM_DATABASE_URL is required for database integration tests")
    return value


@pytest.mark.asyncio
async def test_platform_login_bootstrap_can_run_twice() -> None:
    engine = create_async_engine(make_url(_database_url()))
    role_name = f"bootstrap_test_{uuid.uuid4().hex[:12]}"
    try:
        async with engine.begin() as connection:
            await reconcile_platform_login(connection, password="first-secret", role_name=role_name)
            await reconcile_platform_login(connection, password="second-secret", role_name=role_name)
            exists = await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role_name)"),
                {"role_name": role_name},
            )
            can_connect = await connection.scalar(
                text("SELECT has_database_privilege(:role_name, current_database(), 'CONNECT')"),
                {"role_name": role_name},
            )
            can_create = await connection.scalar(
                text("SELECT has_schema_privilege(:role_name, 'public', 'CREATE')"),
                {"role_name": role_name},
            )
        assert bool(exists)
        assert bool(can_connect)
        assert bool(can_create)
    finally:
        async with engine.begin() as connection:
            role_exists = await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role_name)"),
                {"role_name": role_name},
            )
            if role_exists:
                await connection.execute(text(f'DROP OWNED BY "{role_name}"'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role_name}"'))
        await engine.dispose()
