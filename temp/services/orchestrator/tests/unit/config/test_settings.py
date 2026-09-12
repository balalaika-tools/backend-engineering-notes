"""Orchestrator settings resolution tests."""

import re
from pathlib import Path

import pytest
from orchestrator.config.secrets import Secrets
from orchestrator.config.settings import Settings

SERVICE_ROOT = Path(__file__).parents[3]
VARIABLE_PATTERN = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")


def _documented_required_values() -> dict[str, str]:
    values: dict[str, str] = {}
    in_required = False
    for line in (SERVICE_ROOT / ".env.example").read_text().splitlines():
        if line.startswith("# REQUIRED —"):
            in_required = True
            continue
        if line.startswith("# OVERRIDABLE —"):
            break
        match = VARIABLE_PATTERN.match(line)
        if in_required and match:
            name = match.group(1)
            values[name] = line.split("=", 1)[1]
    return values


def test_missing_environment_name_fails_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT_NAME", raising=False)
    with pytest.raises(ValueError, match="ENVIRONMENT_NAME"):
        Settings(_env_file=None)


def test_documented_required_values_load(monkeypatch: pytest.MonkeyPatch) -> None:
    values = _documented_required_values()
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)
    secrets = Secrets(_env_file=None)

    assert settings.environment_name == "local"
    assert settings.tenant_token == "WEBUI"
    assert settings.max_investigation_batch_size == 100
    assert settings.max_report_batch_size == 10
    assert secrets.platform_db_password.get_secret_value() == "local-platform-password"
    assert secrets.nats_auth_token.get_secret_value() == "local-dev-token"


@pytest.mark.parametrize("environment", ["local", "dev", "staging", "production"])
def test_supported_environment_baselines_resolve(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    for name, value in _documented_required_values().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ENVIRONMENT_NAME", environment)

    settings = Settings(_env_file=None)

    assert settings.environment_name == environment


@pytest.mark.parametrize(
    "key",
    [
        "CTC_POOL_MAX_SIZE",
        "CTC_ACQUISITION_TIMEOUT_SECONDS",
        "CTC_STATEMENT_TIMEOUT_SECONDS",
        "CTC_SCAN_PAGE_SIZE",
        "MAX_BULK_EXCEPTIONS",
    ],
)
def test_bulk_policy_environment_precedence_and_positive_bounds(monkeypatch, key) -> None:
    for name, value in _documented_required_values().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(key, "7")
    assert getattr(Settings(_env_file=None), key.lower()) == 7
    monkeypatch.setenv(key, "0")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "key,value",
    [
        ("CTC_RECONCILIATION_SCHEMA", 'positions"; DROP TABLE x;--'),
        ("CTC_DATABASE_URL", "postgresql+asyncpg://reader:secret-canary@localhost/ctc"),
        ("CTC_DATABASE_URL", "postgresql+asyncpg://reader@localhost/ctc?password=secret-canary"),
    ],
)
def test_ctc_configuration_rejects_unsafe_identifiers_and_credentials(
    monkeypatch, key, value
) -> None:
    for name, required in _documented_required_values().items():
        monkeypatch.setenv(name, required)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError) as raised:
        Settings(_env_file=None)
    assert "secret-canary" not in str(raised.value)


def test_ctc_password_is_redacted(monkeypatch) -> None:
    for name, value in _documented_required_values().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CTC_DB_PASSWORD", "secret-canary")
    secrets = Secrets(_env_file=None)
    assert "secret-canary" not in repr(secrets)
    assert "secret-canary" not in secrets.model_dump_json()
