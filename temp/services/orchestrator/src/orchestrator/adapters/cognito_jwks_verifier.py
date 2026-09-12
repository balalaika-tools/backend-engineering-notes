"""JWT verification against a cached OIDC JWKS document."""

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx
import jwt
from orchestrator.ports.token_verifier import (
    InvalidTokenError,
    MissingScopeError,
    TokenClaims,
    TokenVerifierUnavailableError,
)


class JwksTokenVerifier:
    """Verify Cognito-compatible access tokens with bounded JWKS caching."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        cache_ttl_seconds: float,
        client: httpx.AsyncClient,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._allowed_client_id = audience
        self._cache_ttl_seconds = cache_ttl_seconds
        self._client = client
        self._monotonic = monotonic
        self._keys: dict[str, Mapping[str, Any]] = {}
        self._jwks_url: str | None = None
        self._expires_at = 0.0
        self._forced_refresh_after = 0.0
        self._refresh_lock = asyncio.Lock()

    async def verify(self, token: str, *, required_scope: str) -> TokenClaims:
        kid = self._token_kid(token)
        key = await self._key(kid)
        try:
            payload = jwt.decode(
                token,
                key=jwt.PyJWK.from_dict(dict(key)).key,
                algorithms=["RS256"],
                issuer=self._issuer,
                options={
                    "require": ["exp", "iss", "client_id", "token_use"],
                    "verify_aud": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("Bearer token validation failed") from exc

        if payload.get("token_use") != "access":
            raise InvalidTokenError("Bearer token is not an access token")
        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise InvalidTokenError("Bearer token has no client_id")
        if client_id != self._allowed_client_id:
            raise InvalidTokenError("Bearer token client_id is not allowed")
        scopes = frozenset(str(payload.get("scope", "")).split())
        if required_scope not in scopes:
            raise MissingScopeError(f"Bearer token lacks scope {required_scope!r}")
        return TokenClaims(client_id=client_id, scopes=scopes)

    @staticmethod
    def _token_kid(token: str) -> str:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("Bearer token header is invalid") from exc
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise InvalidTokenError("Bearer token header has no kid")
        return kid

    async def _key(self, kid: str) -> Mapping[str, Any]:
        if self._monotonic() >= self._expires_at:
            await self._refresh()
        key = self._keys.get(kid)
        if key is None and self._monotonic() >= self._forced_refresh_after:
            await self._refresh(force=True)
            self._forced_refresh_after = self._monotonic() + min(
                self._cache_ttl_seconds,
                60.0,
            )
            key = self._keys.get(kid)
        if key is None:
            raise InvalidTokenError("Bearer token references an unknown signing key")
        return key

    async def _refresh(self, *, force: bool = False) -> None:
        async with self._refresh_lock:
            if not force and self._monotonic() < self._expires_at:
                return
            try:
                jwks_url = await self._discover_jwks_url()
                response = await self._client.get(jwks_url)
                response.raise_for_status()
                document = response.json()
                keys = cast(list[Mapping[str, Any]], document["keys"])
                self._keys = {
                    str(key["kid"]): key for key in keys if isinstance(key.get("kid"), str)
                }
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise TokenVerifierUnavailableError("Could not refresh signing keys") from exc
            self._expires_at = self._monotonic() + self._cache_ttl_seconds

    async def _discover_jwks_url(self) -> str:
        if self._jwks_url is not None:
            return self._jwks_url
        response = await self._client.get(f"{self._issuer}/.well-known/openid-configuration")
        response.raise_for_status()
        jwks_url = response.json()["jwks_uri"]
        if not isinstance(jwks_url, str) or not jwks_url:
            raise ValueError("OIDC discovery document has no jwks_uri")
        self._jwks_url = jwks_url
        return jwks_url
