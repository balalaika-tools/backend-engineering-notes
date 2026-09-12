"""Write investigation results through the authenticated CTC REST API."""

import uuid
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from worker.adapters.ctc.client import CtcAuthenticationError, CtcTransientError
from worker.ports.investigation.ctc_write_back import (
    CtcWriteBackUnavailableError,
    WriteBackOutcome,
    WriteBackResult,
)


class CtcHttpClient(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response: ...


class CtcWriteBackAdapter:
    def __init__(self, client: CtcHttpClient) -> None:
        self._client = client

    async def write_comment(
        self,
        *,
        tenant_token: str,
        control_name: str,
        comment: str,
        record_pks: tuple[bytes, ...],
    ) -> WriteBackResult:
        path = f"v1/tenants/{_segment(tenant_token)}/controls/{_segment(control_name)}/comments"
        response = await self._request(
            "POST",
            path,
            json={
                "comment": comment,
                "recordKeys": [_format_pk(value) for value in record_pks],
            },
        )
        return _result(
            response,
            expected_statuses={httpx.codes.OK, httpx.codes.CREATED},
            allow_conflict=False,
        )

    async def write_codes(
        self,
        *,
        tenant_token: str,
        control_name: str,
        exception_name: str,
        exception_pk: bytes,
        exception_version: int,
        reason_code_feature_id: str,
        resolution_code_feature_id: str,
        reason_code: str,
        resolution_code: str,
    ) -> WriteBackResult:
        path = (
            f"rest/v1/tenants/{_segment(tenant_token)}/controls/"
            f"{_segment(control_name)}/exception/actions/update"
        )
        response = await self._request(
            "PATCH",
            path,
            json={
                "exceptionName": exception_name,
                "exceptionPks": [
                    {
                        "pk": _format_pk(exception_pk),
                        "version": exception_version,
                    }
                ],
                "newValues": {
                    reason_code_feature_id: reason_code,
                    resolution_code_feature_id: resolution_code,
                },
            },
        )
        return _result(
            response,
            expected_statuses={httpx.codes.ACCEPTED},
            allow_conflict=True,
        )

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: object,
    ) -> httpx.Response:
        operation = "comment" if path.endswith("/comments") else "codes"
        with trace.get_tracer(__name__).start_as_current_span(
            f"write CTC {operation}",
            record_exception=False,
            attributes={"server.address": "ctc-api", "app.ctc.operation": operation},
        ) as span:
            try:
                response = await self._client.request(method, path, **kwargs)
            except (CtcAuthenticationError, CtcTransientError) as exc:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", type(exc).__name__)
                raise CtcWriteBackUnavailableError(str(exc)) from exc
            if response.status_code == httpx.codes.TOO_MANY_REQUESTS or response.status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", str(response.status_code))
                raise CtcWriteBackUnavailableError(
                    f"CTC write-back returned HTTP {response.status_code}"
                )
            return response


def _result(
    response: httpx.Response,
    *,
    expected_statuses: set[int],
    allow_conflict: bool,
) -> WriteBackResult:
    if response.status_code in expected_statuses:
        return WriteBackResult(WriteBackOutcome.WRITTEN)
    if allow_conflict and response.status_code == httpx.codes.CONFLICT:
        return WriteBackResult(
            WriteBackOutcome.SKIPPED_VERSION_CONFLICT,
            detail=_response_detail(response),
        )
    return WriteBackResult(
        WriteBackOutcome.FAILED,
        detail=_response_detail(response),
    )


def _format_pk(value: bytes) -> str:
    if len(value) != 16:
        raise ValueError("CTC primary keys must contain exactly 16 bytes")
    return str(uuid.UUID(bytes=value))


def _response_detail(response: httpx.Response) -> str:
    detail = response.text.strip()
    if not detail:
        return f"HTTP {response.status_code}"
    return f"HTTP {response.status_code}: {detail[:1000]}"


def _segment(value: str) -> str:
    return quote(value, safe="")
