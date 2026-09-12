"""Contract checks for the documented PostgreSQL schema."""

from platform_db import SQLModel
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

EXPECTED_COLUMNS = {
    "api_requests": {
        "id",
        "client_id",
        "exception_ids",
        "created_at",
        "request_kind",
        "selection_created_at_gte",
        "selection_max_exceptions",
        "selection_schema",
        "selection_exception_name",
    },
    "api_request_investigations": {"request_id", "investigation_id", "position"},
    "investigations": {
        "id",
        "request_id",
        "client_id",
        "exception_id",
        "event_id",
        "status",
        "attempt_count",
        "worker_id",
        "lease_expires_at",
        "last_heartbeat_at",
        "created_at",
        "started_at",
        "completed_at",
        "last_error_code",
        "last_error_message",
        "report",
        "report_uri",
        "analysis",
        "analysis_persisted_at",
        "comment_outcome",
        "comment_written_at",
        "comment_detail",
        "codes_outcome",
        "codes_written_at",
        "codes_detail",
    },
    "outbox_events": {
        "id",
        "event_id",
        "aggregate_type",
        "aggregate_id",
        "event_type",
        "subject",
        "payload",
        "created_at",
        "published_at",
        "attempt_count",
        "next_attempt_at",
        "last_error",
    },
    "config_state": {"id", "generation", "updated_at"},
}


def test_metadata_has_exact_documented_tables_and_columns() -> None:
    assert set(SQLModel.metadata.tables) == set(EXPECTED_COLUMNS)
    for table_name, column_names in EXPECTED_COLUMNS.items():
        assert set(SQLModel.metadata.tables[table_name].columns.keys()) == column_names


def test_postgresql_ddl_contains_checkpoint_types_and_constraints() -> None:
    dialect = postgresql.dialect()
    investigation_ddl = str(
        CreateTable(SQLModel.metadata.tables["investigations"]).compile(dialect=dialect)
    )
    config_ddl = str(CreateTable(SQLModel.metadata.tables["config_state"]).compile(dialect=dialect))

    assert "analysis JSONB" in investigation_ddl
    assert "analysis_persisted_at TIMESTAMP WITH TIME ZONE" in investigation_ddl
    assert "comment_outcome VARCHAR" in investigation_ddl
    assert "codes_outcome VARCHAR" in investigation_ddl
    assert "CONSTRAINT ck_config_state_singleton_id CHECK (id = 1)" in config_ddl

    membership_ddl = str(
        CreateTable(SQLModel.metadata.tables["api_request_investigations"]).compile(dialect=dialect)
    )
    assert "ON DELETE CASCADE" in membership_ddl
    assert "PRIMARY KEY (request_id, investigation_id)" in membership_ddl


def test_partial_indexes_match_the_documented_predicates() -> None:
    dialect = postgresql.dialect()
    indexes = {
        index.name: str(CreateIndex(index).compile(dialect=dialect))
        for table in SQLModel.metadata.tables.values()
        for index in table.indexes
    }

    assert "UNIQUE" in indexes["investigations_active_uniq"]
    assert "WHERE status IN ('queued', 'processing')" in indexes["investigations_active_uniq"]
    assert "WHERE status IN ('queued', 'processing')" in indexes["investigations_status_idx"]
    assert "WHERE published_at IS NULL" in indexes["outbox_pending_idx"]
    assert "WHERE status IN ('completed', 'failed')" in indexes["investigations_retention_idx"]
    assert "WHERE published_at IS NOT NULL" in indexes["outbox_retention_idx"]
