"""Investigation command and query routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from opentelemetry.propagate import inject
from orchestrator.api.dependencies import (
    get_fetch_reports,
    get_query_status,
    get_request_filtered_investigations,
    get_request_investigations,
)
from orchestrator.api.exception_handlers import ApiError
from orchestrator.api.schemas.investigations import (
    FetchReportsRequest,
    FetchReportsResponse,
    InvestigateRequest,
    InvestigateResponse,
    InvestigationReportResponseItem,
    InvestigationStatusResponseItem,
    QueryStatusRequest,
    QueryStatusResponse,
    StructuredAnalysisResponse,
    TriggerInvestigationItem,
    WriteBackResponse,
)
from orchestrator.application.fetch_reports import (
    FetchReports,
    InvalidReportQueryError,
    ReportBatchTooLargeError,
)
from orchestrator.application.query_status import (
    InvalidStatusQueryError,
    QueryStatus,
    StatusBatchTooLargeError,
)
from orchestrator.application.request_filtered_investigations import (
    BulkLimitExceededError,
    InvalidFilteredSelectionError,
    RequestFilteredInvestigations,
)
from orchestrator.application.request_investigations import (
    BatchTooLargeError,
    InvalidBatchError,
    RequestInvestigations,
)
from orchestrator.ports.exception_candidates import CandidateSourceUnavailableError

router = APIRouter(prefix="/v1/agent", tags=["investigations"])


@router.post(
    "/investigate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit exception investigations",
)
async def investigate(
    body: InvestigateRequest,
    request: Request,
    explicit_action: Annotated[RequestInvestigations, Depends(get_request_investigations)],
    filtered_action: Annotated[
        RequestFilteredInvestigations,
        Depends(get_request_filtered_investigations),
    ],
) -> InvestigateResponse:
    carrier: dict[str, str] = {}
    inject(carrier)
    traceparent = carrier.get("traceparent") or request.headers.get("traceparent", "")
    try:
        if body.is_explicit:
            result = await explicit_action.execute(
                client_id=str(request.state.client_id),
                exception_ids=body.exception_ids or [],
                traceparent=traceparent,
            )
        else:
            result = await filtered_action.execute(
                client_id=str(request.state.client_id),
                traceparent=traceparent,
                created_at_gte=body.created_at_gte,
                max_exceptions=body.max_exceptions,
            )
    except BatchTooLargeError as exc:
        raise ApiError(
            code="batch_too_large",
            message=f"Batch exceeds the configured limit of {exc.limit}",
            status_code=400,
        ) from exc
    except InvalidBatchError as exc:
        raise ApiError(code="invalid_batch", message=str(exc), status_code=400) from exc
    except BulkLimitExceededError as exc:
        raise ApiError(code=exc.error_code, message=str(exc), status_code=400) from exc
    except InvalidFilteredSelectionError as exc:
        raise ApiError(code="invalid_request", message=str(exc), status_code=422) from exc
    except CandidateSourceUnavailableError as exc:
        raise ApiError(
            code=exc.error_code,
            message="CTC candidate selection is temporarily unavailable",
            status_code=503,
        ) from exc

    investigations = [
        TriggerInvestigationItem(
            investigation_id=item.investigation_id,
            exception_id=item.exception_id,
            status=item.status,
            created=item.created,
        )
        for item in result.investigations
    ]
    return InvestigateResponse(
        request_id=result.request_id,
        selected_count=len(investigations),
        status_url=str(
            request.app.url_path_for(
                "query_investigation_request_status",
                request_id=str(result.request_id),
            )
        ),
        investigations=investigations,
    )


@router.post("/investigations/status", summary="Look up investigation status")
async def query_status(
    body: QueryStatusRequest,
    action: Annotated[QueryStatus, Depends(get_query_status)],
) -> QueryStatusResponse:
    try:
        result = await action.execute(
            exception_ids=body.exception_ids,
            investigation_ids=body.investigation_ids,
        )
    except StatusBatchTooLargeError as exc:
        raise ApiError(
            code="batch_too_large",
            message=f"Batch exceeds the configured limit of {exc.limit}",
            status_code=400,
        ) from exc
    except InvalidStatusQueryError as exc:
        raise ApiError(code="invalid_batch", message=str(exc), status_code=400) from exc

    return QueryStatusResponse(
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
        ]
    )


@router.post("/investigations/reports", summary="Retrieve investigation reports")
async def fetch_reports(
    body: FetchReportsRequest,
    action: Annotated[FetchReports, Depends(get_fetch_reports)],
) -> FetchReportsResponse:
    try:
        result = await action.execute(
            exception_ids=body.exception_ids,
            investigation_ids=body.investigation_ids,
        )
    except ReportBatchTooLargeError as exc:
        raise ApiError(
            code="batch_too_large",
            message=f"Batch exceeds the configured limit of {exc.limit}",
            status_code=400,
        ) from exc
    except InvalidReportQueryError as exc:
        raise ApiError(code="invalid_batch", message=str(exc), status_code=400) from exc

    return FetchReportsResponse(
        reports=[
            InvestigationReportResponseItem(
                investigation_id=item.investigation_id,
                exception_id=item.exception_id,
                status=item.status,
                completed_at=item.completed_at,
                last_error_code=item.last_error_code,
                analysis=(
                    StructuredAnalysisResponse(
                        reason_code=item.analysis.reason_code,
                        resolution_code=item.analysis.resolution_code,
                        confidence=item.analysis.confidence,
                        short_analysis=item.analysis.short_analysis,
                        explanation=item.analysis.explanation,
                        reasoning=item.analysis.reasoning,
                        write_back=WriteBackResponse(
                            comment=item.analysis.write_back.comment,
                            codes=item.analysis.write_back.codes,
                            detail=item.analysis.write_back.detail,
                        ),
                    )
                    if item.analysis
                    else None
                ),
                report=item.report,
                report_uri=item.report_uri,
            )
            for item in result.reports
        ]
    )
