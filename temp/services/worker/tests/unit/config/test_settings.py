"""Worker settings resolution and invariant tests."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from worker.config.secrets import Secrets
from worker.config.settings import Settings

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


def _set_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _documented_required_values().items():
        monkeypatch.setenv(name, value)


def test_missing_environment_name_fails_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT_NAME", raising=False)
    with pytest.raises(ValueError, match="ENVIRONMENT_NAME"):
        Settings(_env_file=None)


def test_documented_required_values_load(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_environment(monkeypatch)

    settings = Settings(_env_file=None)
    secrets = Secrets(_env_file=None)

    assert settings.environment_name == "local"
    assert settings.deployment_release_id == "local-v1"
    assert settings.tenant_token == "WEBUI"
    assert settings.max_ack_pending == 75
    assert secrets.ctc_client_secret.get_secret_value() == "local-ctc-client-secret"
    assert secrets.nats_auth_token.get_secret_value() == "local-dev-token"


@pytest.mark.parametrize("environment", ["local", "dev", "staging", "production"])
def test_supported_environment_baselines_resolve(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT_NAME", environment)

    settings = Settings(_env_file=None)

    assert settings.environment_name == environment


def test_dev_policy_enables_both_ctc_writebacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT_NAME", "dev")

    settings = Settings(_env_file=None)

    assert settings.log_level == "INFO"
    assert settings.shutdown_grace_seconds == 90
    assert settings.comment_write_enabled is True
    assert settings.codes_write_enabled is True


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("ACK_WAIT_SECONDS", "40", "ACK_WAIT_SECONDS"),
        ("LEASE_SECONDS", "60", "LEASE_SECONDS"),
        ("MAX_DELIVER", "5", "MAX_DELIVER"),
        (
            "CONTROL_CONTEXT_REBUILD_WAIT_SECONDS",
            "600",
            "CONTROL_CONTEXT_REBUILD_WAIT_SECONDS",
        ),
    ],
)
def test_invalid_timing_combination_refuses_startup(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    message: str,
) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None)
