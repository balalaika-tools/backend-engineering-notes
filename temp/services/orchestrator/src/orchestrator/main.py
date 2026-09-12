"""ASGI entry point."""

import uvicorn
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from platform_observability import shutdown_observability

from orchestrator.bootstrap.app import create_app
from orchestrator.bootstrap.observability import configure_observability
from orchestrator.config.settings import get_settings
from orchestrator.observability.metrics import initialize_metrics

settings = get_settings()
try:
    configure_observability(settings)
    initialize_metrics()
    HTTPXClientInstrumentor().instrument()
    app = create_app()
    FastAPIInstrumentor.instrument_app(app)
except BaseException:
    shutdown_observability()
    raise


def main() -> None:
    """Run Uvicorn without replacing the process-wide structured log handlers."""
    uvicorn.run(app, host="0.0.0.0", port=8080, log_config=None)


if __name__ == "__main__":
    main()
