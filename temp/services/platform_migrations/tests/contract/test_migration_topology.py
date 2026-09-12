"""Static ownership and topology checks for the single Alembic history."""

import re
from pathlib import Path

MIGRATION_ROOT = Path(__file__).parents[2] / "alembic"
REPOSITORY_ROOT = Path(__file__).parents[4]


def test_migration_history_has_one_linear_head() -> None:
    revisions = sorted((MIGRATION_ROOT / "versions").glob("*.py"))
    assert len(revisions) == 5

    revision_text = revisions[0].read_text()
    assert 'revision: str = "20260907_0001"' in revision_text
    assert "down_revision: str | None = None" in revision_text
    assert 'sa.Column("generation", sa.BigInteger(), server_default="0", nullable=False)' in (
        revision_text
    )
    assert re.search(r"INSERT INTO config_state \(id\) VALUES \(1\)", revision_text)
    index_text = revisions[1].read_text()
    assert 'revision: str = "20260908_0002"' in index_text
    assert 'down_revision: str | None = "20260907_0001"' in index_text
    membership_text = revisions[2].read_text()
    assert 'revision: str = "20260908_0003"' in membership_text
    assert 'down_revision: str | None = "20260908_0002"' in membership_text
    assert '"api_request_investigations"' in membership_text
    assert "ON CONFLICT DO NOTHING" in membership_text
    detail_text = revisions[3].read_text()
    assert 'revision: str = "20260909_0004"' in detail_text
    assert 'down_revision: str | None = "20260908_0003"' in detail_text
    assert '"comment_detail"' in detail_text
    filtered_text = revisions[4].read_text()
    assert 'revision: str = "20260911_0005"' in filtered_text
    assert 'down_revision: str | None = "20260909_0004"' in filtered_text


def test_async_environment_enables_type_and_default_comparison() -> None:
    environment = (MIGRATION_ROOT / "env.py").read_text()
    assert "async_engine_from_config" in environment
    assert "compare_type=True" in environment
    assert "compare_server_default=True" in environment


def test_migrations_are_owned_by_the_dedicated_deployable() -> None:
    shared_library = REPOSITORY_ROOT / "libs" / "platform_db"

    assert not (shared_library / "alembic").exists()
    assert not (shared_library / "alembic.ini").exists()
    assert "alembic" not in (shared_library / "pyproject.toml").read_text()
