"""Migration database credential-composition tests."""

import pytest
from platform_migrations.database_url import (
    MigrationConfigurationError,
    resolve_database_url,
)


def test_reserved_password_characters_round_trip() -> None:
    password = "p@ss:/%?#[] word"

    database_url = resolve_database_url(
        {
            "PLATFORM_DATABASE_URL": "postgresql+asyncpg://platform@db.internal/platform",
            "PLATFORM_DB_PASSWORD": password,
        }
    )

    assert database_url.password == password
    assert database_url.username == "platform"
    assert database_url.host == "db.internal"


def test_direct_integration_url_does_not_require_split_password() -> None:
    database_url = resolve_database_url(
        {
            "INTEGRATION_PLATFORM_DATABASE_URL": (
                "postgresql+asyncpg://test-user:test-password@localhost/test-platform"
            )
        }
    )

    assert database_url.password == "test-password"
    assert database_url.database == "test-platform"


@pytest.mark.parametrize(
    ("environment", "missing_variable"),
    [
        ({}, "PLATFORM_DATABASE_URL"),
        (
            {"PLATFORM_DATABASE_URL": "postgresql+asyncpg://platform@db.internal/platform"},
            "PLATFORM_DB_PASSWORD",
        ),
    ],
)
def test_missing_runtime_variables_fail_by_safe_name(
    environment: dict[str, str],
    missing_variable: str,
) -> None:
    with pytest.raises(MigrationConfigurationError, match=missing_variable):
        resolve_database_url(environment)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "postgresql+asyncpg://platform:hunter2@db.internal/platform",
        "not-a-url hunter2",
    ],
)
def test_configuration_errors_do_not_disclose_url_contents(unsafe_url: str) -> None:
    with pytest.raises(MigrationConfigurationError) as error:
        resolve_database_url(
            {
                "PLATFORM_DATABASE_URL": unsafe_url,
                "PLATFORM_DB_PASSWORD": "another-secret",
            }
        )

    message = str(error.value)
    assert "hunter2" not in message
    assert "another-secret" not in message
