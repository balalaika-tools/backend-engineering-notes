"""Create the initial platform schema.

Revision ID: 20260907_0001
Revises: None
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260907_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_VALUES = ("queued", "processing", "completed", "failed")


def upgrade() -> None:
    status_enum = postgresql.ENUM(*STATUS_VALUES, name="investigation_status", create_type=False)
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "api_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("exception_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_requests")),
    )
    op.create_index("api_requests_created_at_idx", "api_requests", ["created_at"])

    op.create_table(
        "config_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_config_state_singleton_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_state")),
    )
    op.execute(sa.text("INSERT INTO config_state (id) VALUES (1)"))

    op.create_table(
        "investigations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("exception_id", sa.String(), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", status_enum, server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("worker_id", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("report_uri", sa.Text(), nullable=True),
        sa.Column("analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("analysis_persisted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment_outcome", sa.String(), nullable=True),
        sa.Column("comment_written_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("codes_outcome", sa.String(), nullable=True),
        sa.Column("codes_written_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("codes_detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["api_requests.id"],
            name=op.f("fk_investigations_request_id_api_requests"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investigations")),
        sa.UniqueConstraint("event_id", name=op.f("uq_investigations_event_id")),
    )
    active = "status IN ('queued', 'processing')"
    op.create_index(
        "investigations_active_uniq",
        "investigations",
        ["exception_id"],
        unique=True,
        postgresql_where=sa.text(active),
    )
    op.create_index(
        "investigations_exception_id_idx",
        "investigations",
        ["exception_id", sa.text("created_at DESC")],
    )
    op.create_index("investigations_request_idx", "investigations", ["request_id"])
    op.create_index(
        "investigations_status_idx",
        "investigations",
        ["status"],
        postgresql_where=sa.text(active),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_outbox_events_event_id")),
    )
    op.create_index(
        "outbox_pending_idx",
        "outbox_events",
        ["next_attempt_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("outbox_pending_idx", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("investigations_status_idx", table_name="investigations")
    op.drop_index("investigations_request_idx", table_name="investigations")
    op.drop_index("investigations_exception_id_idx", table_name="investigations")
    op.drop_index("investigations_active_uniq", table_name="investigations")
    op.drop_table("investigations")
    op.drop_table("config_state")
    op.drop_index("api_requests_created_at_idx", table_name="api_requests")
    op.drop_table("api_requests")
    postgresql.ENUM(*STATUS_VALUES, name="investigation_status").drop(
        op.get_bind(), checkfirst=True
    )
