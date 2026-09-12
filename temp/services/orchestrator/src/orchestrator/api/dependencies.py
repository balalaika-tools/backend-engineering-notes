"""FastAPI dependency adapters."""

from typing import cast

from fastapi import Request
from orchestrator.application.fetch_reports import FetchReports
from orchestrator.application.query_status import QueryInvestigationRequestStatus, QueryStatus
from orchestrator.application.request_filtered_investigations import RequestFilteredInvestigations
from orchestrator.application.request_investigations import RequestInvestigations
from orchestrator.application.reset_configuration import ResetConfiguration
from orchestrator.bootstrap.runtime import RuntimeState


def get_runtime(request: Request) -> RuntimeState:
    return cast(RuntimeState, request.app.state.runtime)


def get_request_investigations(request: Request) -> RequestInvestigations:
    return get_runtime(request).actions.request_investigations


def get_query_status(request: Request) -> QueryStatus:
    return get_runtime(request).actions.query_status


def get_query_investigation_request_status(
    request: Request,
) -> QueryInvestigationRequestStatus:
    return get_runtime(request).actions.query_request_status


def get_fetch_reports(request: Request) -> FetchReports:
    return get_runtime(request).actions.fetch_reports


def get_reset_configuration(request: Request) -> ResetConfiguration:
    return get_runtime(request).actions.reset_configuration


def get_request_filtered_investigations(request: Request) -> RequestFilteredInvestigations:
    return get_runtime(request).actions.request_filtered_investigations
