"""JWKS verifier behavior with an in-process HTTP transport."""

import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from orchestrator.adapters.cognito_jwks_verifier import JwksTokenVerifier
from orchestrator.ports.token_verifier import InvalidTokenError, MissingScopeError

ISSUER = "https://issuer.example/default"
ALLOWED_CLIENT_ID = "test-client"
TOKEN_AUDIENCE = "exception-investigation"


def _key_pair(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    public_jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return private, public_jwk


def _token(
    private: rsa.RSAPrivateKey,
    kid: str,
    *,
    issuer: str = ISSUER,
    expires_at: int | None = None,
    scope: str = "investigations/read investigations/write",
    client_id: str = ALLOWED_CLIENT_ID,
    include_audience: bool = True,
) -> str:
    payload = {
        "iss": issuer,
        "exp": expires_at or int(time.time()) + 300,
        "client_id": client_id,
        "token_use": "access",
        "scope": scope,
    }
    if include_audience:
        payload["aud"] = TOKEN_AUDIENCE
    return jwt.encode(payload, private, algorithm="RS256", headers={"kid": kid})


def _verifier(handler: Callable[[httpx.Request], httpx.Response]) -> JwksTokenVerifier:
    def oidc_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"jwks_uri": f"{ISSUER}/jwks"})
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(oidc_handler))
    return JwksTokenVerifier(
        issuer=ISSUER,
        audience=ALLOWED_CLIENT_ID,
        cache_ttl_seconds=300,
        client=client,
    )


@pytest.mark.asyncio
async def test_valid_token_returns_trusted_claims() -> None:
    private, jwk = _key_pair("current")
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, json={"keys": [jwk]})

    verifier = _verifier(handler)

    claims = await verifier.verify(_token(private, "current"), required_scope="investigations/read")

    assert claims.client_id == "test-client"
    assert "investigations/write" in claims.scopes
    assert requested_paths == ["/default/jwks"]


@pytest.mark.asyncio
async def test_cognito_access_token_without_audience_is_valid() -> None:
    private, jwk = _key_pair("current")
    verifier = _verifier(lambda _request: httpx.Response(200, json={"keys": [jwk]}))

    claims = await verifier.verify(
        _token(private, "current", include_audience=False),
        required_scope="investigations/read",
    )

    assert claims.client_id == "test-client"


@pytest.mark.asyncio
async def test_validly_signed_token_from_unapproved_client_is_invalid() -> None:
    private, jwk = _key_pair("current")
    verifier = _verifier(lambda _request: httpx.Response(200, json={"keys": [jwk]}))

    with pytest.raises(InvalidTokenError, match="client_id"):
        await verifier.verify(
            _token(private, "current", client_id="unapproved-client"),
            required_scope="investigations/read",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token_factory",
    [
        lambda private: _token(private, "current", expires_at=int(time.time()) - 1),
        lambda private: _token(private, "current", issuer="https://wrong.example"),
    ],
)
async def test_expired_or_wrong_issuer_is_invalid(
    token_factory: Callable[[rsa.RSAPrivateKey], str],
) -> None:
    private, jwk = _key_pair("current")
    verifier = _verifier(lambda _request: httpx.Response(200, json={"keys": [jwk]}))

    with pytest.raises(InvalidTokenError):
        await verifier.verify(token_factory(private), required_scope="investigations/read")


@pytest.mark.asyncio
async def test_missing_scope_is_forbidden() -> None:
    private, jwk = _key_pair("current")
    verifier = _verifier(lambda _request: httpx.Response(200, json={"keys": [jwk]}))

    with pytest.raises(MissingScopeError):
        await verifier.verify(
            _token(private, "current", scope="investigations/read"),
            required_scope="investigations/write",
        )


@pytest.mark.asyncio
async def test_unknown_kid_forces_one_refresh() -> None:
    old_private, old_jwk = _key_pair("old")
    new_private, new_jwk = _key_pair("new")
    del old_private
    responses = iter(([old_jwk], [old_jwk, new_jwk]))
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"keys": next(responses)})

    verifier = _verifier(handler)
    claims = await verifier.verify(_token(new_private, "new"), required_scope="investigations/read")

    assert claims.client_id == "test-client"
    assert calls == 2


@pytest.mark.asyncio
async def test_unknown_kid_after_refresh_is_invalid() -> None:
    private, _jwk = _key_pair("missing")
    verifier = _verifier(lambda _request: httpx.Response(200, json={"keys": []}))

    with pytest.raises(InvalidTokenError, match="unknown signing key"):
        await verifier.verify(_token(private, "missing"), required_scope="investigations/read")


@pytest.mark.asyncio
async def test_repeated_unknown_kids_do_not_force_repeated_refreshes() -> None:
    private, _jwk = _key_pair("missing")
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"keys": []})

    verifier = _verifier(handler)
    for kid in ("missing-one", "missing-two"):
        with pytest.raises(InvalidTokenError):
            await verifier.verify(_token(private, kid), required_scope="investigations/read")

    assert calls == 2
