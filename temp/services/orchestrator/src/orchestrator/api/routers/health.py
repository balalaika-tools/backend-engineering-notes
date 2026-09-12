"""Unauthenticated liveness and readiness routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from orchestrator.api.dependencies import get_runtime
from orchestrator.bootstrap.runtime import RuntimeState

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness(
    runtime: Annotated[RuntimeState, Depends(get_runtime)],
) -> JSONResponse:
    database_ready = await runtime.database_ready()
    publisher_ready = runtime.publisher_ready()
    ready = database_ready and publisher_ready
    content = {
        "status": "ready" if ready else "not_ready",
        "checks": {
            "database": "ok" if database_ready else "failed",
            "outbox_publisher": "ok" if publisher_ready else "failed",
        },
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=content,
    )
