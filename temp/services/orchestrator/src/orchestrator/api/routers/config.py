"""Operator configuration endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from orchestrator.api.dependencies import get_reset_configuration
from orchestrator.api.schemas.config import (
    ResetConfigurationRequest,
    ResetConfigurationResponse,
)
from orchestrator.application.reset_configuration import ResetConfiguration

router = APIRouter(prefix="/v1/control-context", tags=["configuration"])


@router.post("/refresh", summary="Refresh the shared control context")
async def reset_configuration(
    action: Annotated[ResetConfiguration, Depends(get_reset_configuration)],
    body: ResetConfigurationRequest | None = None,
) -> ResetConfigurationResponse:
    result = await action.execute(purge_manuals=body.purge_manuals if body else False)
    return ResetConfigurationResponse(generation=result.generation)
