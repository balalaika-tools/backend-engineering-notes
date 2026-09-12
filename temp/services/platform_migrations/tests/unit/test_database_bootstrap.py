"""Safe configuration tests for the platform database bootstrap command."""

import json

import pytest
from platform_migrations.database_bootstrap import (
    DatabaseBootstrapError,
    load_bootstrap_configuration,
)


def test_admin_secret_and_application_password_remain_split() -> None:
    configuration = load_bootstrap_configuration(
        {
            "PLATFORM_DATABASE_URL": "postgresql+asyncpg://platform@db.internal:5432/platform",
            "PLATFORM_DB_PASSWORD": "app p@ss:/?#[]",
            "RDS_ADMIN_SECRET_JSON": json.dumps(
                {
                    "username": "platform_admin",
                    "password": "admin p@ss:/?#[]",
                    "host": "db.internal",
                    "port": 5432,
                }
            ),
        }
    )

    assert configuration.admin_database_url.username == "platform_admin"
    assert configuration.admin_database_url.password == "admin p@ss:/?#[]"
    assert configuration.platform_password == "app p@ss:/?#[]"


def test_rds_managed_secret_uses_endpoint_from_application_url() -> None:
    configuration = load_bootstrap_configuration(
        {
            "PLATFORM_DATABASE_URL": "postgresql+asyncpg://platform@db.internal:5432/platform",
            "PLATFORM_DB_PASSWORD": "application-secret",
            "RDS_ADMIN_SECRET_JSON": json.dumps(
                {"username": "platform_admin", "password": "admin-secret"}
            ),
        }
    )

    assert configuration.admin_database_url.host == "db.internal"
    assert configuration.admin_database_url.port == 5432


@pytest.mark.parametrize(
    "admin_secret",
    [
        "not-json-super-secret",
        json.dumps({"username": "admin", "password": "super-secret", "host": "wrong"}),
    ],
)
def test_invalid_admin_secret_errors_are_redacted(admin_secret: str) -> None:
    with pytest.raises(DatabaseBootstrapError) as error:
        load_bootstrap_configuration(
            {
                "PLATFORM_DATABASE_URL": "postgresql+asyncpg://platform@db.internal/platform",
                "PLATFORM_DB_PASSWORD": "application-secret",
                "RDS_ADMIN_SECRET_JSON": admin_secret,
            }
        )

    message = str(error.value)
    assert "super-secret" not in message
    assert "application-secret" not in message
    assert "not-json" not in message
