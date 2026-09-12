"""Focused drift checks for the published OpenAPI and written API reference."""

from pathlib import Path

from orchestrator.bootstrap.app import create_app

ROOT = Path(__file__).parents[5]
BUSINESS_OPERATIONS = {
    ("post", "/v1/agent/investigate"),
    ("get", "/v1/agent/investigations/{request_id}"),
    ("post", "/v1/agent/investigations/status"),
    ("post", "/v1/agent/investigations/reports"),
    ("post", "/v1/control-context/refresh"),
}


def test_openapi_contains_canonical_operations_summaries_and_submission_examples() -> None:
    schema = create_app().openapi()
    operations = {
        (method, path)
        for path, path_operations in schema["paths"].items()
        if path.startswith("/v1/")
        for method in path_operations
    }

    assert operations == BUSINESS_OPERATIONS
    for method, path in BUSINESS_OPERATIONS:
        assert schema["paths"][path][method]["summary"]

    examples = schema["components"]["schemas"]["InvestigateRequest"]["examples"]
    assert {"exception_ids": ["37889927", "37889928"]} in examples
    assert {"created_at_gte": "2026-09-01T00:00:00Z", "max_exceptions": 20} in examples
    assert {} in examples


def test_api_reference_exists_at_canonical_name_and_covers_runtime_inventory() -> None:
    reference = (ROOT / "docs/api-contracts.md").read_text()
    app = create_app()
    documented_runtime_paths = {getattr(route, "path", None) for route in app.routes}
    application_paths = set(app.openapi()["paths"])

    assert not (ROOT / "docs/frontend-api-contracts.md").exists()
    for _method, path in BUSINESS_OPERATIONS:
        assert path in reference
    for route in (
        "/oauth2/token",
        "/health",
        "/ready",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
    ):
        assert route in reference
        if route in {"/health", "/ready"}:
            assert route in application_paths
        elif route != "/oauth2/token":
            assert route in documented_runtime_paths
    for contract_value in (
        "selected_count",
        "skipped_disabled",
        "256 KB",
        "max_report_batch_size",
        "Route migration",
        "Worker SQL availability policy",
    ):
        assert contract_value in reference
