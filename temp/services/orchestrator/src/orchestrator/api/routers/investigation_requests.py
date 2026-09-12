"""Polling route for accepted investigation requests."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from orchestrator.api.dependencies import get_query_investigation_request_status
from orchestrator.api.exception_handlers import ApiError
from orchestrator.api.schemas.investigations import (
    InvestigationRequestStatusResponse,
    InvestigationRequestStatusSummaryResponse,
    InvestigationStatusResponseItem,
)
from orchestrator.application.query_status import (
    InvestigationRequestNotFoundError,
    QueryInvestigationRequestStatus,
)

router = APIRouter(
    prefix="/v1/agent/investigations",
    tags=["investigation requests"],
)


@router.get("/{request_id}", summary="Poll an investigation request")
async def query_investigation_request_status(
    request_id: uuid.UUID,
    request: Request,
    action: Annotated[
        QueryInvestigationRequestStatus,
        Depends(get_query_investigation_request_status),
    ],
) -> InvestigationRequestStatusResponse:
    try:
        result = await action.execute(
            request_id=request_id,
            client_id=str(request.state.client_id),
        )
    except InvestigationRequestNotFoundError as exc:
        raise ApiError(
            code="request_not_found",
            message="Investigation request was not found",
            status_code=404,
        ) from exc

    return InvestigationRequestStatusResponse(
        request_id=result.request_id,
        status=result.status,
        summary=InvestigationRequestStatusSummaryResponse(
            total=result.summary.total,
            in_progress=result.summary.in_progress,
            completed=result.summary.completed,
            failed=result.summary.failed,
        ),
        investigations=[
            InvestigationStatusResponseItem(
                investigation_id=item.investigation_id,
                exception_id=item.exception_id,
                status=item.status,
                attempt_count=item.attempt_count,
                created_at=item.created_at,
                started_at=item.started_at,
                completed_at=item.completed_at,
                last_error_code=item.last_error_code,
            )
            for item in result.investigations
        ],
    )
