"""Worker executable entry point."""

import asyncio
import signal

from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from platform_observability import shutdown_observability

from worker.bootstrap.observability import configure_observability
from worker.bootstrap.runtime import build_runtime
from worker.config.settings import get_settings
from worker.observability.metrics import initialize_metrics


async def _run() -> None:
    settings = get_settings()
    try:
        configure_observability(settings)
        initialize_metrics()
        HTTPXClientInstrumentor().instrument()
        runtime = await build_runtime(settings=settings)
    except BaseException:
        shutdown_observability()
        raise
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, runtime.stop.set)
    await runtime.run()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
