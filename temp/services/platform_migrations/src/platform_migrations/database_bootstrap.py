"""Idempotently reconcile the platform application's PostgreSQL login and grants."""

import asyncio
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any

from sqlalchemy import URL, make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


class DatabaseBootstrapError(RuntimeError):
    """A safely reportable database-bootstrap failure."""


@dataclass(frozen=True, slots=True)
class BootstrapConfiguration:
    admin_database_url: URL
    platform_password: str


def load_bootstrap_configuration(environment: Mapping[str, str]) -> BootstrapConfiguration:
    """Resolve split application/admin credentials without retaining secret payloads in errors."""
    platform_url = _required(environment, "PLATFORM_DATABASE_URL")
    platform_password = _required(environment, "PLATFORM_DB_PASSWORD")
    secret_json = _required(environment, "RDS_ADMIN_SECRET_JSON")

    try:
        parsed_url = make_url(platform_url)
        secret = json.loads(secret_json)
        username = _secret_string(secret, "username")
        password = _secret_string(secret, "password")
        if not parsed_url.host:
            raise DatabaseBootstrapError("PLATFORM_DATABASE_URL must contain a host")
        host = secret.get("host", parsed_url.host)
        if not isinstance(host, str) or not host:
            raise DatabaseBootstrapError("RDS admin secret host is invalid")
        port = int(secret.get("port", parsed_url.port or 5432))
    except (JSONDecodeError, TypeError, ValueError, DatabaseBootstrapError):
        raise DatabaseBootstrapError("RDS_ADMIN_SECRET_JSON is invalid; payload redacted") from None

    if parsed_url.password is not None:
        raise DatabaseBootstrapError("PLATFORM_DATABASE_URL must not contain a password")
    if parsed_url.host != host or (parsed_url.port or 5432) != port:
        raise DatabaseBootstrapError(
            "RDS_ADMIN_SECRET_JSON endpoint does not match PLATFORM_DATABASE_URL; payload redacted"
        )
    if not parsed_url.database:
        raise DatabaseBootstrapError("PLATFORM_DATABASE_URL must select a database")

    return BootstrapConfiguration(
        admin_database_url=parsed_url.set(username=username, password=password, host=host, port=port),
        platform_password=platform_password,
    )


async def reconcile_platform_login(
    connection: AsyncConnection,
    *,
    password: str,
    role_name: str = "platform",
) -> None:
    """Create or update one login and reconcile the grants needed by migrations/runtime."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role_name):
        raise DatabaseBootstrapError("role_name must be a safe PostgreSQL identifier")

    exists = bool(
        await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role_name)"),
            {"role_name": role_name},
        )
    )
    action = "ALTER ROLE %I LOGIN PASSWORD %L" if exists else "CREATE ROLE %I LOGIN PASSWORD %L"
    await _execute_formatted(connection, action, role_name, password)

    grants = (
        "GRANT CONNECT ON DATABASE %I TO %I",
        "GRANT USAGE, CREATE ON SCHEMA public TO %I",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I",
        "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I",
    )
    database_name = str(connection.engine.url.database)
    await _execute_formatted(connection, grants[0], database_name, role_name)
    for statement in grants[1:]:
        await _execute_formatted(connection, statement, role_name)


async def _execute_formatted(
    connection: AsyncConnection,
    template: str,
    *values: str,
) -> None:
    placeholders = ", ".join(
        f"CAST(:value_{index} AS text)" for index in range(len(values))
    )
    parameters = {f"value_{index}": value for index, value in enumerate(values)}
    statement = await connection.scalar(
        text(f"SELECT format(CAST(:template AS text), {placeholders})"),
        {"template": template, **parameters},
    )
    if not isinstance(statement, str):
        raise DatabaseBootstrapError("PostgreSQL did not produce a bootstrap statement")
    await connection.exec_driver_sql(statement)


async def bootstrap_database(configuration: BootstrapConfiguration) -> None:
    engine = create_async_engine(configuration.admin_database_url, echo=False)
    try:
        async with engine.begin() as connection:
            await reconcile_platform_login(connection, password=configuration.platform_password)
    except SQLAlchemyError:
        raise DatabaseBootstrapError("Database bootstrap failed; credentials redacted") from None
    finally:
        await engine.dispose()


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        raise DatabaseBootstrapError(f"{name} is required")
    return value


def _secret_string(secret: Any, key: str) -> str:
    if not isinstance(secret, dict) or not isinstance(secret.get(key), str) or not secret[key]:
        raise DatabaseBootstrapError(f"RDS admin secret is missing {key}")
    return str(secret[key])


def main() -> int:
    try:
        configuration = load_bootstrap_configuration(os.environ)
        asyncio.run(bootstrap_database(configuration))
    except DatabaseBootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Platform database login and grants reconciled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
