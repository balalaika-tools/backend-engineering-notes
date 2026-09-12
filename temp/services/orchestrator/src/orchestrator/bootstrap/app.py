"""FastAPI application factory and lifespan."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from orchestrator.api.exception_handlers import register_exception_handlers
from orchestrator.api.middleware.authentication import AuthenticationMiddleware
from orchestrator.api.routers.config import router as config_router
from orchestrator.api.routers.health import router as health_router
from orchestrator.api.routers.investigation_requests import router as request_status_router
from orchestrator.api.routers.investigations import router as investigations_router
from orchestrator.bootstrap.runtime import RuntimeState, runtime

RuntimeFactory = Callable[[], AbstractAsyncContextManager[RuntimeState]]


def create_app(runtime_factory: RuntimeFactory = runtime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with runtime_factory() as runtime_state:
            app.state.runtime = runtime_state
            yield

    app = FastAPI(title="Exception Investigation Orchestrator", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(investigations_router)
    app.include_router(request_status_router)
    app.include_router(config_router)
    app.add_middleware(AuthenticationMiddleware)
    return app
