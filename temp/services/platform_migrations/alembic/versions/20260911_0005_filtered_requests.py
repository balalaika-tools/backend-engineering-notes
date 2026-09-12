"""Add filtered selection audit metadata without changing request membership."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0005"
down_revision: str | None = "20260909_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_requests",
        sa.Column("request_kind", sa.String(), nullable=False, server_default="explicit"),
    )
    op.add_column("api_requests", sa.Column("selection_created_at_gte", sa.DateTime(timezone=True)))
    op.add_column("api_requests", sa.Column("selection_max_exceptions", sa.Integer()))
    op.add_column("api_requests", sa.Column("selection_schema", sa.String()))
    op.add_column("api_requests", sa.Column("selection_exception_name", sa.String()))


def downgrade() -> None:
    for name in (
        "selection_exception_name",
        "selection_schema",
        "selection_max_exceptions",
        "selection_created_at_gte",
        "request_kind",
    ):
        op.drop_column("api_requests", name)
