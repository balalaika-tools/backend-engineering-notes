"""Database URL resolution for the migration deployable."""

from collections.abc import Mapping

from sqlalchemy import URL, make_url
from sqlalchemy.exc import ArgumentError


class MigrationConfigurationError(RuntimeError):
    """Raised when the migration database contract is incomplete or unsafe."""


def _parse_url(value: str, *, variable: str) -> URL:
    try:
        return make_url(value)
    except ArgumentError:
        raise MigrationConfigurationError(
            f"{variable} is not a valid SQLAlchemy database URL"
        ) from None


def resolve_database_url(environment: Mapping[str, str]) -> URL:
    """Resolve the authenticated migration URL without exposing credentials in errors."""
    platform_url = environment.get("PLATFORM_DATABASE_URL")
    if platform_url:
        password = environment.get("PLATFORM_DB_PASSWORD")
        if not password:
            raise MigrationConfigurationError(
                "PLATFORM_DB_PASSWORD is required when PLATFORM_DATABASE_URL is set"
            )
        parsed = _parse_url(platform_url, variable="PLATFORM_DATABASE_URL")
        if parsed.password is not None:
            raise MigrationConfigurationError("PLATFORM_DATABASE_URL must not contain a password")
        return parsed.set(password=password)

    integration_url = environment.get("INTEGRATION_PLATFORM_DATABASE_URL")
    if integration_url:
        return _parse_url(
            integration_url,
            variable="INTEGRATION_PLATFORM_DATABASE_URL",
        )

    raise MigrationConfigurationError(
        "PLATFORM_DATABASE_URL (or INTEGRATION_PLATFORM_DATABASE_URL in tests) is required"
    )
