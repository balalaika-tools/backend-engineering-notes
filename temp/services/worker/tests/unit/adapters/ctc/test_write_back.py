"""CTC write-back request construction and failure translation."""

import json
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
from worker.adapters.ctc.client import CtcClient
from worker.adapters.ctc.write_back import CtcWriteBackAdapter
from worker.ports.investigation.ctc_write_back import (
    CtcWriteBackUnavailableError,
    WriteBackOutcome,
)

ISSUER_URL = "https://issuer.example/oauth2/token"
API_BASE_URL = "https://api.example/services/secure"
EXCEPTION_PK = uuid.UUID("01a066f0-9c41-72a5-b648-08d1584f6712").bytes
REASON_FEATURE_ID = "7099e75b-9ffc-4a89-b0f6-631b56a821e1"
RESOLUTION_FEATURE_ID = "1774b347-716f-4fe1-90f1-5602c2234f3a"
RECORD_PKS = (
    uuid.UUID("11111111-1111-1111-1111-111111111111").bytes,
    uuid.UUID("22222222-2222-2222-2222-222222222222").bytes,
)


def _adapter(
    handler: (
        Callable[[httpx.Request], httpx.Response]
        | Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]]
    ),
) -> CtcWriteBackAdapter:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = CtcClient(
        http_client=http_client,
        issuer_url=ISSUER_URL,
        api_base_url=API_BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
    )
    return CtcWriteBackAdapter(client)


def _token_or(handler: Callable[[httpx.Request], httpx.Response]):
    def route(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(ISSUER_URL):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        return handler(request)

    return route


@pytest.mark.asyncio
@pytest.mark.parametrize("success_status", [200, 201])
async def test_comment_accepts_documented_success_statuses(success_status: int) -> None:
    def api(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/v1/tenants/WEBUI/controls/Positions/comments")
        assert json.loads(request.content) == {
            "comment": "Confidence: High\nThe break was explained.",
            "recordKeys": [
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            ],
        }
        return httpx.Response(success_status)

    result = await _adapter(_token_or(api)).write_comment(
        tenant_token="WEBUI",
        control_name="Positions",
        comment="Confidence: High\nThe break was explained.",
        record_pks=RECORD_PKS,
    )

    assert result.outcome is WriteBackOutcome.WRITTEN


@pytest.mark.asyncio
async def test_202_code_update_posts_both_codes_and_version() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path.endswith(
            "/rest/v1/tenants/WEBUI/controls/Positions/exception/actions/update"
        )
        assert json.loads(request.content) == {
            "exceptionName": "Break",
            "exceptionPks": [{"pk": "01a066f0-9c41-72a5-b648-08d1584f6712", "version": 7}],
            "newValues": {
                REASON_FEATURE_ID: "AccountBlocked",
                RESOLUTION_FEATURE_ID: "Custodian",
            },
        }
        return httpx.Response(202)

    result = await _adapter(_token_or(api)).write_codes(
        tenant_token="WEBUI",
        control_name="Positions",
        exception_name="Break",
        exception_pk=EXCEPTION_PK,
        exception_version=7,
        reason_code_feature_id=REASON_FEATURE_ID,
        resolution_code_feature_id=RESOLUTION_FEATURE_ID,
        reason_code="AccountBlocked",
        resolution_code="Custodian",
    )

    assert result.outcome is WriteBackOutcome.WRITTEN


@pytest.mark.asyncio
async def test_409_code_update_maps_to_skipped_version_conflict() -> None:
    adapter = _adapter(_token_or(lambda _request: httpx.Response(409, text="version changed")))

    result = await adapter.write_codes(
        tenant_token="WEBUI",
        control_name="Positions",
        exception_name="Break",
        exception_pk=EXCEPTION_PK,
        exception_version=7,
        reason_code_feature_id=REASON_FEATURE_ID,
        resolution_code_feature_id=RESOLUTION_FEATURE_ID,
        reason_code="AccountBlocked",
        resolution_code="Custodian",
    )

    assert result.outcome is WriteBackOutcome.SKIPPED_VERSION_CONFLICT
    assert result.detail == "HTTP 409: version changed"


@pytest.mark.asyncio
async def test_400_code_update_is_a_failed_permanent_outcome() -> None:
    adapter = _adapter(_token_or(lambda _request: httpx.Response(400, text="unknown feature")))

    result = await adapter.write_codes(
        tenant_token="WEBUI",
        control_name="Positions",
        exception_name="Break",
        exception_pk=EXCEPTION_PK,
        exception_version=7,
        reason_code_feature_id=REASON_FEATURE_ID,
        resolution_code_feature_id=RESOLUTION_FEATURE_ID,
        reason_code="AccountBlocked",
        resolution_code="Custodian",
    )

    assert result.outcome is WriteBackOutcome.FAILED
    assert result.detail == "HTTP 400: unknown feature"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_temporary_response_is_retryable(status_code: int) -> None:
    adapter = _adapter(_token_or(lambda _request: httpx.Response(status_code)))

    with pytest.raises(CtcWriteBackUnavailableError):
        await adapter.write_comment(
            tenant_token="WEBUI",
            control_name="Positions",
            comment="analysis",
            record_pks=RECORD_PKS,
        )


@pytest.mark.asyncio
async def test_401_refreshes_once_before_the_write_succeeds() -> None:
    issued_tokens = 0
    api_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued_tokens
        if request.url == httpx.URL(ISSUER_URL):
            issued_tokens += 1
            return httpx.Response(
                200,
                json={"access_token": f"token-{issued_tokens}", "expires_in": 300},
            )
        api_tokens.append(request.headers["authorization"])
        return httpx.Response(401 if len(api_tokens) == 1 else 200)

    result = await _adapter(handler).write_comment(
        tenant_token="WEBUI",
        control_name="Positions",
        comment="analysis",
        record_pks=RECORD_PKS,
    )

    assert result.outcome is WriteBackOutcome.WRITTEN
    assert api_tokens == ["Bearer token-1", "Bearer token-2"]


@pytest.mark.asyncio
async def test_transport_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(ISSUER_URL):
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(CtcWriteBackUnavailableError):
        await _adapter(handler).write_comment(
            tenant_token="WEBUI",
            control_name="Positions",
            comment="analysis",
            record_pks=RECORD_PKS,
        )


@pytest.mark.asyncio
async def test_rejects_non_16_byte_database_keys_before_writing() -> None:
    adapter = _adapter(_token_or(lambda _request: httpx.Response(200)))

    with pytest.raises(ValueError, match="16 bytes"):
        await adapter.write_comment(
            tenant_token="WEBUI",
            control_name="Positions",
            comment="analysis",
            record_pks=(b"short",),
        )
