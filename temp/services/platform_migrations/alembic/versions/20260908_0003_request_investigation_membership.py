"""Track every investigation returned for an API request.

Revision ID: 20260908_0003
Revises: 20260908_0002
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260908_0003"
down_revision: str | None = "20260908_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_request_investigations",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            name=op.f("fk_api_request_investigations_investigation_id_investigations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["api_requests.id"],
            name=op.f("fk_api_request_investigations_request_id_api_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "request_id",
            "investigation_id",
            name=op.f("pk_api_request_investigations"),
        ),
        sa.UniqueConstraint(
            "request_id",
            "position",
            name=op.f("uq_api_request_investigations_request_id"),
        ),
    )
    op.create_index(
        "api_request_investigations_investigation_idx",
        "api_request_investigations",
        ["investigation_id"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO api_request_investigations
                (request_id, investigation_id, position)
            SELECT
                requests.id,
                matched.id,
                CAST(requested.position - 1 AS INTEGER)
            FROM api_requests AS requests
            CROSS JOIN LATERAL unnest(requests.exception_ids)
                WITH ORDINALITY AS requested(exception_id, position)
            JOIN LATERAL (
                SELECT investigations.id
                FROM investigations
                WHERE investigations.exception_id = requested.exception_id
                ORDER BY
                    (investigations.request_id = requests.id) DESC,
                    (investigations.created_at <= requests.created_at) DESC,
                    CASE
                        WHEN investigations.created_at <= requests.created_at
                        THEN investigations.created_at
                    END DESC,
                    CASE
                        WHEN investigations.created_at > requests.created_at
                        THEN investigations.created_at
                    END ASC
                LIMIT 1
            ) AS matched ON true
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "api_request_investigations_investigation_idx",
        table_name="api_request_investigations",
    )
    op.drop_table("api_request_investigations")
