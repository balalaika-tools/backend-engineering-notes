"""CTC OAuth token caching and API host routing."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
from worker.adapters.ctc.client import CtcClient, CtcTransientError

ISSUER_URL = "https://issuer.example/oauth2/token"
API_BASE_URL = "https://api.example/services/secure"


def _client(
    handler: (
        Callable[[httpx.Request], httpx.Response]
        | Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]]
    ),
    *,
    clock: Callable[[], float] = lambda: 0.0,
) -> CtcClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return CtcClient(
        http_client=http_client,
        issuer_url=ISSUER_URL,
        api_base_url=API_BASE_URL,
        client_id="client-id",
        client_secret="client-secret",
        clock=clock,
    )


@pytest.mark.asyncio
async def test_reuses_token_and_sends_api_calls_to_the_separate_base_host() -> None:
    token_requests = 0
    api_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url == httpx.URL(ISSUER_URL):
            token_requests += 1
            assert request.headers["authorization"].startswith("Basic ")
            assert request.content == b"grant_type=client_credentials"
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 300})
        api_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)

    await client.request("GET", "/v1/inventory/controls")
    await client.request("GET", "v1/decode-sets")

    assert token_requests == 1
    assert [request.url for request in api_requests] == [
        httpx.URL(f"{API_BASE_URL}/v1/inventory/controls"),
        httpx.URL(f"{API_BASE_URL}/v1/decode-sets"),
    ]
    assert {request.headers["authorization"] for request in api_requests} == {"Bearer token-1"}


@pytest.mark.asyncio
async def test_refreshes_at_eighty_percent_of_token_lifetime() -> None:
    now = [0.0]
    issued_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(ISSUER_URL):
            token = f"token-{len(issued_tokens) + 1}"
            issued_tokens.append(token)
            return httpx.Response(200, json={"access_token": token, "expires_in": 300})
        return httpx.Response(200)

    client = _client(handler, clock=lambda: now[0])

    await client.request("GET", "v1/a")
    now[0] = 239.99
    await client.request("GET", "v1/b")
    now[0] = 240.0
    await client.request("GET", "v1/c")

    assert issued_tokens == ["token-1", "token-2"]


@pytest.mark.asyncio
async def test_refreshes_once_after_401_and_retries_with_the_new_token() -> None:
    issued_tokens: list[str] = []
    api_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(ISSUER_URL):
            token = f"token-{len(issued_tokens) + 1}"
            issued_tokens.append(token)
            return httpx.Response(200, json={"access_token": token, "expires_in": 300})
        api_tokens.append(request.headers["authorization"])
        return httpx.Response(401 if len(api_tokens) == 1 else 200)

    client = _client(handler)

    response = await client.request("POST", "v1/comments", json={"text": "analysis"})

    assert response.status_code == 200
    assert issued_tokens == ["token-1", "token-2"]
    assert api_tokens == ["Bearer token-1", "Bearer token-2"]


@pytest.mark.asyncio
async def test_second_401_is_transient_and_is_not_retried_again() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request.url == httpx.URL(ISSUER_URL):
            return httpx.Response(
                200, json={"access_token": f"token-{request_count}", "expires_in": 300}
            )
        return httpx.Response(401)

    client = _client(handler)

    with pytest.raises(CtcTransientError):
        await client.request("GET", "v1/inventory/controls")

    assert request_count == 4


@pytest.mark.asyncio
async def test_concurrent_cold_requests_share_one_token_request() -> None:
    token_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url == httpx.URL(ISSUER_URL):
            token_requests += 1
            await asyncio.sleep(0)
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 300})
        return httpx.Response(200)

    client = _client(handler)

    await asyncio.gather(*(client.request("GET", f"v1/items/{index}") for index in range(10)))

    assert token_requests == 1


@pytest.mark.asyncio
async def test_absolute_api_path_cannot_override_the_configured_host() -> None:
    client = _client(lambda _request: httpx.Response(200))

    with pytest.raises(ValueError, match="relative"):
        await client.request("GET", "https://issuer.example/services/secure/v1/items")
