"""OTLP/HTTP export through a disposable local protocol endpoint."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

pytestmark = pytest.mark.integration

REPOSITORY = Path(__file__).parents[4]
LIBRARY_SOURCE = REPOSITORY / "libs/platform_observability/src"


class _CaptureHandler(BaseHTTPRequestHandler):
    requests: dict[str, list[bytes]] = defaultdict(list)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers["content-length"])
        self.requests[self.path].append(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _resource_attributes(resource: object) -> dict[str, object]:
    attributes = resource.attributes
    return {item.key: item.value.string_value for item in attributes}


@pytest.mark.parametrize("service_name", ["orchestrator", "worker"])
def test_exports_one_correlated_signal_set_with_explicit_identity(service_name: str) -> None:
    _CaptureHandler.requests = defaultdict(list)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(LIBRARY_SOURCE)
    script = """
        import io
        import structlog
        from opentelemetry import metrics, trace
        from platform_observability import (
            LoggingConfig,
            TelemetryConfig,
            configure_logging,
            configure_observability,
            shutdown_observability,
        )

        providers = configure_observability(
            TelemetryConfig(
                service_name=SERVICE,
                service_namespace="exception-investigation",
                service_version="acceptance",
                service_instance_id=f"{SERVICE}-1",
                environment_name="local",
                endpoint=ENDPOINT,
                export_interval_millis=60_000,
            )
        )
        assert providers is not None
        configure_logging(
            LoggingConfig(SERVICE, "INFO", True),
            logger_provider=providers.logger,
            stream=io.StringIO(),
        )
        counter = metrics.get_meter("acceptance").create_counter("acceptance_total")
        with trace.get_tracer("acceptance").start_as_current_span("acceptance operation"):
            counter.add(1)
            structlog.get_logger("acceptance").info("acceptance_completed")
        assert shutdown_observability(timeout_seconds=3) == ()
    """.replace("SERVICE", repr(service_name)).replace("ENDPOINT", repr(endpoint))
    try:
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(script)],
            cwd=REPOSITORY,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.returncode == 0, result.stderr
    assert {path: len(payloads) for path, payloads in _CaptureHandler.requests.items()} == {
        "/v1/logs": 1,
        "/v1/metrics": 1,
        "/v1/traces": 1,
    }
    trace_request = ExportTraceServiceRequest.FromString(_CaptureHandler.requests["/v1/traces"][0])
    metric_request = ExportMetricsServiceRequest.FromString(
        _CaptureHandler.requests["/v1/metrics"][0]
    )
    log_request = ExportLogsServiceRequest.FromString(_CaptureHandler.requests["/v1/logs"][0])
    resources = (
        trace_request.resource_spans[0].resource,
        metric_request.resource_metrics[0].resource,
        log_request.resource_logs[0].resource,
    )
    assert all(
        _resource_attributes(resource)["service.name"] == service_name for resource in resources
    )
    assert trace_request.resource_spans[0].scope_spans[0].spans[0].name == "acceptance operation"
    assert metric_request.resource_metrics[0].scope_metrics[0].metrics[0].name == "acceptance_total"
    log_record = log_request.resource_logs[0].scope_logs[0].log_records[0]
    assert "acceptance_completed" in log_record.body.string_value
