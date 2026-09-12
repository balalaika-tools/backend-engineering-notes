"""Add partial indexes for periodic retention deletes.

Revision ID: 20260908_0002
Revises: 20260907_0001
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0002"
down_revision: str | None = "20260907_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "investigations_retention_idx",
        "investigations",
        ["completed_at"],
        postgresql_where=sa.text("status IN ('completed', 'failed')"),
    )
    op.create_index(
        "outbox_retention_idx",
        "outbox_events",
        ["published_at"],
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("outbox_retention_idx", table_name="outbox_events")
    op.drop_index("investigations_retention_idx", table_name="investigations")
