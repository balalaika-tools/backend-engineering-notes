"""Configuration-document contracts for the orchestrator."""

import re
from pathlib import Path

import yaml
from orchestrator.config.secrets import Secrets
from orchestrator.config.settings import YAML_POLICY_FIELDS, Settings

SERVICE_ROOT = Path(__file__).parents[3]
ROOT = SERVICE_ROOT.parents[1]
SECTION_PATTERN = re.compile(r"^# (REQUIRED|OVERRIDABLE|OPTIONAL) —")
VARIABLE_PATTERN = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")
PASSTHROUGH_OPTIONAL = {"ORCHESTRATOR_CONFIG_DIR"}


def _aliases(model: type[Settings] | type[Secrets], fields: set[str]) -> set[str]:
    return {str(model.model_fields[name].alias) for name in fields}


def _sections(path: Path) -> dict[str, set[str]]:
    sections = {"REQUIRED": set(), "OVERRIDABLE": set(), "OPTIONAL": set()}
    current: str | None = None
    for line in path.read_text().splitlines():
        header = SECTION_PATTERN.match(line)
        if header:
            current = header.group(1)
            continue
        variable = VARIABLE_PATTERN.match(line)
        if current and variable:
            sections[current].add(variable.group(1))
    return sections


def _merged_policy(environment: str) -> dict[str, object]:
    paths = [
        ROOT / "config" / "base.yaml",
        ROOT / "config" / f"{environment}.yaml",
        ROOT / "config" / "services" / "orchestrator.yaml",
        ROOT / "config" / "services" / f"orchestrator.{environment}.yaml",
    ]
    merged: dict[str, object] = {}
    for path in paths:
        if path.is_file():
            merged.update(yaml.safe_load(path.read_text()) or {})
    return merged


def test_service_env_example_matches_settings_and_secrets() -> None:
    sections = _sections(SERVICE_ROOT / ".env.example")
    policy_aliases = _aliases(Settings, set(YAML_POLICY_FIELDS))
    required_fields = {
        name for name, field in Settings.model_fields.items() if field.is_required()
    } - set(YAML_POLICY_FIELDS)
    required_secrets = {name for name, field in Secrets.model_fields.items() if field.is_required()}
    optional_secrets = {
        name for name, field in Secrets.model_fields.items() if not field.is_required()
    }
    optional_fields = {
        name for name, field in Settings.model_fields.items() if not field.is_required()
    }

    assert sections["REQUIRED"] == _aliases(Settings, required_fields) | _aliases(
        Secrets, required_secrets
    )
    assert sections["OVERRIDABLE"] == policy_aliases
    assert sections["OPTIONAL"] == (
        _aliases(Settings, optional_fields)
        | _aliases(Secrets, optional_secrets)
        | PASSTHROUGH_OPTIONAL
    )


def test_every_yaml_baseline_has_exactly_the_policy_fields() -> None:
    for environment in ("local", "dev", "staging", "production"):
        assert set(_merged_policy(environment)) == set(YAML_POLICY_FIELDS)


def test_dev_yaml_baselines_exist() -> None:
    assert (ROOT / "config" / "dev.yaml").is_file()
    assert (ROOT / "config" / "services" / "orchestrator.dev.yaml").is_file()


def test_yaml_baselines_are_owned_by_repository_root() -> None:
    assert (ROOT / "config" / "base.yaml").is_file()
    assert not (SERVICE_ROOT / "config").exists()


def test_root_env_documents_all_required_orchestrator_inputs() -> None:
    root_variables = {
        match.group(1)
        for line in (ROOT / ".env.example").read_text().splitlines()
        if (match := VARIABLE_PATTERN.match(line))
    }
    required = _sections(SERVICE_ROOT / ".env.example")["REQUIRED"]
    assert required <= root_variables
