"""Configuration-document contracts for the migration deployable."""

import re
from pathlib import Path

SERVICE_ROOT = Path(__file__).parents[2]
ROOT = SERVICE_ROOT.parents[1]
SECTION_PATTERN = re.compile(r"^# (REQUIRED|BOOTSTRAP_REQUIRED|OVERRIDABLE|OPTIONAL) —")
VARIABLE_PATTERN = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")


def _sections(path: Path) -> dict[str, set[str]]:
    sections: dict[str, set[str]] = {
        "REQUIRED": set(),
        "BOOTSTRAP_REQUIRED": set(),
        "OVERRIDABLE": set(),
        "OPTIONAL": set(),
    }
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


def test_migration_env_example_matches_runtime_contract() -> None:
    assert _sections(SERVICE_ROOT / ".env.example") == {
        "REQUIRED": {"PLATFORM_DATABASE_URL", "PLATFORM_DB_PASSWORD"},
        "BOOTSTRAP_REQUIRED": {"RDS_ADMIN_SECRET_JSON"},
        "OVERRIDABLE": set(),
        "OPTIONAL": set(),
    }


def test_root_env_documents_all_required_migration_inputs() -> None:
    root_variables = {
        match.group(1)
        for line in (ROOT / ".env.example").read_text().splitlines()
        if (match := VARIABLE_PATTERN.match(line))
    }

    assert _sections(SERVICE_ROOT / ".env.example")["REQUIRED"] <= root_variables
