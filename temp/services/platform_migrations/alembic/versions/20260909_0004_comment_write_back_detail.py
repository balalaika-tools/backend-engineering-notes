"""Persist failure detail for the comment write-back checkpoint.

Revision ID: 20260909_0004
Revises: 20260908_0003
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0004"
down_revision: str | None = "20260908_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("investigations", sa.Column("comment_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("investigations", "comment_detail")
