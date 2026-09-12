"""Authenticated HTTP access to the CTC API."""

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from worker.observability.metrics import ctc_api_requests


class CtcAuthenticationError(RuntimeError):
    """The CTC token endpoint could not provide usable credentials."""


class CtcTransientError(RuntimeError):
    """A CTC operation may succeed when the investigation is retried."""

    def __init__(self, message: str, *, response: httpx.Response | None = None) -> None:
        self.response = response
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _CachedToken:
    value: str
    refresh_at: float


class CtcClient:
    """Cache OAuth credentials and route API traffic to the separate API host."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        issuer_url: str,
        api_base_url: str,
        client_id: str,
        client_secret: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http_client = http_client
        self._issuer_url = httpx.URL(issuer_url)
        self._api_base_url = httpx.URL(api_base_url)
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock
        self._token: _CachedToken | None = None
        self._token_lock = asyncio.Lock()

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send one API request, refreshing and replaying once after a 401."""
        url = self._api_url(path)
        token = await self._access_token()
        try:
            response = await self._send(method, url, token, headers=headers, **kwargs)
        except CtcTransientError:
            _record_transport_error(path)
            raise
        _record_request(path, response)
        if response.status_code != httpx.codes.UNAUTHORIZED:
            return response

        self._invalidate_token(token)
        refreshed_token = await self._access_token()
        try:
            response = await self._send(method, url, refreshed_token, headers=headers, **kwargs)
        except CtcTransientError:
            _record_transport_error(path)
            raise
        _record_request(path, response)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise CtcTransientError(
                "CTC API rejected both the cached and refreshed access tokens",
                response=response,
            )
        return response

    async def _access_token(self) -> str:
        token = self._token
        if token is not None and self._clock() < token.refresh_at:
            return token.value

        async with self._token_lock:
            token = self._token
            if token is not None and self._clock() < token.refresh_at:
                return token.value
            self._token = await self._fetch_token()
            return self._token.value

    async def _fetch_token(self) -> _CachedToken:
        try:
            response = await self._http_client.post(
                self._issuer_url,
                auth=httpx.BasicAuth(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials"},
            )
        except httpx.TransportError as exc:
            raise CtcTransientError("CTC token request failed") from exc

        if response.status_code == httpx.codes.TOO_MANY_REQUESTS or response.status_code >= 500:
            raise CtcTransientError(
                "CTC token endpoint is temporarily unavailable", response=response
            )
        if response.is_error:
            raise CtcAuthenticationError(
                f"CTC token endpoint rejected the client with HTTP {response.status_code}"
            )

        access_token, expires_in = _parse_token_response(response)
        # The production token lifetime is 300 seconds; 80% refreshes it at 240 seconds.
        return _CachedToken(
            value=access_token,
            refresh_at=self._clock() + (expires_in * 0.8),
        )

    async def _send(
        self,
        method: str,
        url: httpx.URL,
        token: str,
        *,
        headers: Mapping[str, str] | None,
        **kwargs: Any,
    ) -> httpx.Response:
        request_headers = httpx.Headers(headers)
        request_headers["Authorization"] = f"Bearer {token}"
        try:
            return await self._http_client.request(
                method,
                url,
                headers=request_headers,
                **kwargs,
            )
        except httpx.TransportError as exc:
            raise CtcTransientError("CTC API request failed") from exc

    def _invalidate_token(self, rejected_token: str) -> None:
        if self._token is not None and self._token.value == rejected_token:
            self._token = None

    def _api_url(self, path: str) -> httpx.URL:
        parts = urlsplit(path)
        if (
            parts.scheme
            or parts.netloc
            or any(segment == ".." for segment in parts.path.split("/"))
        ):
            raise ValueError("CTC API path must be relative to the configured API base URL")
        return httpx.URL(f"{str(self._api_base_url).rstrip('/')}/{path.lstrip('/')}")


def _parse_token_response(response: httpx.Response) -> tuple[str, float]:
    try:
        body = response.json()
        access_token = body["access_token"]
        expires_in = float(body["expires_in"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CtcAuthenticationError("CTC token response has an invalid shape") from exc
    if not isinstance(access_token, str) or not access_token or expires_in <= 0:
        raise CtcAuthenticationError("CTC token response has an invalid shape")
    return access_token, expires_in


def _endpoint(path: str) -> str:
    if path.endswith("/comments"):
        return "comments"
    if path.endswith("/exception/actions/update"):
        return "exception_update"
    if path.endswith("/decode-sets"):
        return "decode_sets"
    if "/configuration" in path:
        return "configuration"
    return "other"


def _record_request(path: str, response: httpx.Response) -> None:
    ctc_api_requests.add(
        1,
        {"endpoint": _endpoint(path), "status": str(response.status_code)},
    )


def _record_transport_error(path: str) -> None:
    ctc_api_requests.add(
        1,
        {"endpoint": _endpoint(path), "status": "transport_error"},
    )
