# JWT — JSON Web Tokens

> A self-contained, cryptographically signed token. The server signs it; anyone with the public key can verify it without calling the server again.

---

## The Problem JWT Solves

**Old way (sessions):** On login, store a session ID in a database. On every request, look it up. Problem: every server needs access to that database — it becomes a bottleneck and a single point of failure.

**JWT way:** Put the proof directly in the token. The token contains all the info your API needs (who the user is, what they can do, when it expires). Sign it with a private key so no one can tamper with it. Now your API can verify tokens **without calling any external service.**

```
Session approach:     Request → lookup session_id in DB → get user info
JWT approach:         Request → verify token signature (local, fast) → read claims
```

---

## JWT Structure

A JWT is three chunks of JSON, individually base64url-encoded, joined by dots:

```
eyJhbGciOiJSUzI1NiIsImtpZCI6ImFiYzEyMyJ9  .  eyJzdWIiOiJ1c2VyLXV1aWQiLCJleHAiOjE3MDB9  .  <signature>
              HEADER                                          PAYLOAD                              SIGNATURE
```

### Header

```json
{
  "alg": "RS256",
  "kid": "abc123def456"
}
```

| Field | Meaning |
|-------|---------|
| `alg` | Signing algorithm — `RS256` (RSA + SHA-256) or `ES256` (ECDSA) for asymmetric; `HS256` for symmetric (shared secret) |
| `kid` | Key ID — tells the verifier which public key was used to sign this token. Important because issuers rotate keys. |

### Payload (Claims)

```json
{
  "sub":   "a1b2c3d4-uuid",
  "iss":   "https://auth.example.com/",
  "aud":   "my-api-client-id",
  "exp":   1700003600,
  "iat":   1700000000,
  "jti":   "unique-token-id",
  "scope": "read write",
  "email": "alice@example.com"
}
```

**Claims** are what the token asserts. The issuer puts them there; your API verifies the signature then reads them.

### Standard Claims Reference

| Claim | Full Name | Meaning |
|-------|-----------|---------|
| `sub` | Subject | Who the token is about — a user UUID or client ID |
| `iss` | Issuer | Who created the token — the auth server's URL |
| `aud` | Audience | Who the token is intended for — your API or client ID |
| `exp` | Expiration | Unix timestamp — token invalid after this time |
| `iat` | Issued At | Unix timestamp — when the token was created |
| `nbf` | Not Before | Unix timestamp — token invalid before this time |
| `jti` | JWT ID | Unique identifier for this token — used for revocation |

Everything else (email, name, scope, groups, etc.) is a custom claim specific to the issuer.

### Signature

Created by the issuer using its **private key**:

```
SIGNATURE = RSA_SIGN(
    private_key,
    base64url(header) + "." + base64url(payload)
)
```

Your API verifies using the corresponding **public key** (published by the issuer). If anyone modifies even one character of the header or payload, the signature won't match and validation fails.

The payload is **not encrypted** — base64url encoding is reversible. Anyone who gets the token can read the claims. Never put secrets in JWT claims.

---

## JWKS — JSON Web Key Set

The issuer publishes its public keys at a well-known URL called the **JWKS endpoint**. Your API fetches this to verify tokens.

```
https://auth.example.com/.well-known/jwks.json
```

The response is a set of public keys:

```json
{
  "keys": [
    {
      "kid": "abc123def456",
      "kty": "RSA",
      "alg": "RS256",
      "use": "sig",
      "n":   "very-long-base64url-number",
      "e":   "AQAB"
    },
    {
      "kid": "xyz789",
      "kty": "RSA",
      "alg": "RS256",
      "use": "sig",
      "n":   "another-long-base64url-number",
      "e":   "AQAB"
    }
  ]
}
```

### The Trust Chain

```
Issuer has:  PRIVATE key (secret, never shared)
Issuer publishes: PUBLIC keys at JWKS endpoint

Token creation: issuer signs with private key
Token verification: your API verifies with matching public key

If signature valid → issuer definitely created this token, nobody tampered
```

**Cache the JWKS.** It changes rarely (only when the issuer rotates keys). Fetching it per-request adds latency and creates a dependency on the auth server for every API call. Cache with a TTL of 15-60 minutes and refresh on `kid` miss.

### Key Rotation

Issuers rotate signing keys periodically. The `kid` in the token header tells you which key to use. When you see a `kid` not in your cache, refetch the JWKS. If it's still not there after a fresh fetch, the token is invalid.

---

## OpenID Connect Discovery

OpenID Connect (OIDC) is an identity layer built on top of OAuth 2.0. OIDC issuers publish a **discovery document** that tells clients everything they need:

```
https://auth.example.com/.well-known/openid-configuration
```

Response:

```json
{
  "issuer": "https://auth.example.com",
  "authorization_endpoint": "https://auth.example.com/oauth2/authorize",
  "token_endpoint": "https://auth.example.com/oauth2/token",
  "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
  "userinfo_endpoint": "https://auth.example.com/oauth2/userinfo",
  "response_types_supported": ["code", "token"],
  "grant_types_supported": ["authorization_code", "client_credentials", "refresh_token"],
  "scopes_supported": ["openid", "email", "profile"],
  "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"]
}
```

Instead of hardcoding every URL, a client only needs the discovery URL. It fetches this document on startup and learns where to get tokens, where to find public keys, etc.

---

## Validation Algorithm

When your API receives `Authorization: Bearer <token>`, validate in this order:

```
1. Split the token into header.payload.signature
2. Base64url-decode the header → get kid and alg
3. Fetch the JWKS (from cache or jwks_uri) → find the key matching kid
4. Verify the signature using the public key
   → If invalid: reject (token is tampered or from wrong issuer)
5. Decode the payload (no verification needed after step 4)
6. Check exp > now() - leeway (see below)
   → If expired: reject with 401
7. Check iss == your expected issuer URL
   → If wrong: reject (token from a different auth server)
8. Check aud (or client_id for OAuth access tokens) == your expected value
   → If wrong: reject (token not intended for your API)
9. (Optional) Check token_use, scope, groups — your authorization logic
```

**Order matters.** Verify the signature before reading any claims. A tampered payload could have any exp or iss value.

### Clock-Skew Leeway on `exp` / `nbf` / `iat`

Clocks drift. The issuer and your API do not share a wall clock, and even with NTP the difference between two machines is often tens to hundreds of milliseconds — worse across cloud regions or on clients behind bad NTP. Without leeway, a token that is *just barely* issued or about to expire can fail validation on one of the two sides and succeed on the other.

RFC 7519 §4.1.4 explicitly allows implementers to accept **"some small leeway, usually no more than a few minutes"** on `exp` (and by symmetry `nbf`, `iat`). The conventional value is **0–60 seconds** — large enough to absorb typical NTP skew, small enough that an attacker re-using a just-expired token barely gets any extra window.

```python
# PyJWT: use the `leeway` argument
jwt.decode(token, signing_key.key, algorithms=["RS256"], issuer=ISSUER, leeway=30)

# python-jose: same option
from jose import jwt as jose_jwt
jose_jwt.decode(token, key, algorithms=["RS256"], options={"leeway": 30})
```

Pick a single value for your service (30s is a reasonable default), apply it to `exp`, `nbf`, and `iat` uniformly, and do not extend it further without a specific reason — every second of leeway is extra time a stolen token stays valid after its nominal expiry.

---

## Python Utilities

### Decode Without Verification (debugging only)

```python
import base64
import json


def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verifying. For debugging only — never trust the result."""
    payload = token.split(".")[1]
    # Restore base64 padding
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def decode_jwt_header(token: str) -> dict:
    header = token.split(".")[0]
    header += "=" * (4 - len(header) % 4)
    return json.loads(base64.urlsafe_b64decode(header))
```

### Check Expiry

```python
import time


def is_token_expired(token: str, buffer_seconds: int = 0) -> bool:
    """Return True if the token is expired (or will expire within buffer_seconds)."""
    claims = decode_jwt_payload(token)
    return claims["exp"] - time.time() < buffer_seconds


def seconds_until_expiry(token: str) -> float:
    claims = decode_jwt_payload(token)
    return claims["exp"] - time.time()
```

### Full Validation with PyJWT

```python
import jwt
from jwt import PyJWKClient
from typing import Optional

# Initialize once at startup — handles caching internally
jwks_client = PyJWKClient(
    "https://auth.example.com/.well-known/jwks.json",
    cache_jwk_set=True,
    lifespan=3600,  # refresh cache every hour
)


def validate_token(
    token: str,
    *,
    issuer: str,
    audience: Optional[str] = None,
    algorithms: Optional[list[str]] = None,
) -> dict:
    """
    Validate a JWT and return the decoded claims.
    Raises jwt.exceptions.InvalidTokenError on any validation failure.
    """
    algorithms = algorithms or ["RS256"]
    signing_key = jwks_client.get_signing_key_from_jwt(token)

    return jwt.decode(
        token,
        signing_key.key,
        algorithms=algorithms,
        issuer=issuer,
        audience=audience,
        options={
            "verify_exp": True,
            "verify_iss": True,
            "verify_aud": audience is not None,
        },
    )
```

### Full Validation with python-jose

> **Prefer PyJWT.** `python-jose` is effectively unmaintained (no release since 2021) and has shipped with vulnerable transitive dependencies; FastAPI dropped it from its docs in favor of PyJWT. Use the PyJWT example above for new code. This example is kept only because a lot of existing Cognito/FastAPI code still uses `jose`. If you need JWE/encrypted tokens (which PyJWT does not do), prefer the actively maintained [`joserfc`](https://jose.authlib.org/) or `authlib` over `python-jose`.

Note also that the example below verifies the signature manually and then reads claims with `get_unverified_claims`. That is correct *only* because the signature is checked first on the same token bytes — keep that ordering. PyJWT's `jwt.decode` does signature + claim validation in one call and is harder to get wrong.

```python
from jose import jwk, jwt as jose_jwt
from jose.utils import base64url_decode
from typing import Optional
import urllib.request
import json
import time


_jwks_cache: Optional[dict] = None


def get_jwks(jwks_url: str) -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        with urllib.request.urlopen(jwks_url) as f:
            _jwks_cache = json.loads(f.read())
    return _jwks_cache


def validate_token(
    token: str,
    *,
    jwks_url: str,
    issuer: str,
    audience: Optional[str] = None,
    token_use: Optional[str] = None,  # e.g. "access" or "id" (Cognito-specific)
) -> dict:
    headers = jose_jwt.get_unverified_headers(token)
    kid = headers["kid"]

    jwks = get_jwks(jwks_url)
    key_data = next((k for k in jwks["keys"] if k["kid"] == kid), None)
    if not key_data:
        raise ValueError(f"Public key not found for kid={kid!r}")

    public_key = jwk.construct(key_data)
    message, encoded_sig = token.rsplit(".", 1)
    decoded_sig = base64url_decode(encoded_sig.encode())

    if not public_key.verify(message.encode(), decoded_sig):
        raise ValueError("Token signature verification failed")

    claims = jose_jwt.get_unverified_claims(token)

    if claims["exp"] < time.time():
        raise ValueError("Token is expired")
    if claims["iss"] != issuer:
        raise ValueError(f"Invalid issuer: expected {issuer!r}, got {claims['iss']!r}")
    if audience and claims.get("aud") != audience:
        raise ValueError("Invalid audience")
    if token_use and claims.get("token_use") != token_use:
        raise ValueError(f"Invalid token_use: expected {token_use!r}")

    return claims
```

### FastAPI Dependency

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient

bearer_scheme = HTTPBearer()
jwks_client = PyJWKClient("https://auth.example.com/.well-known/jwks.json")

ISSUER = "https://auth.example.com/"
AUDIENCE = "my-api"


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    token = credentials.credentials
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            leeway=30,  # absorb clock skew on exp/nbf/iat — see "Clock-Skew Leeway"
        )
        return claims
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


# Usage
@app.get("/me")
async def get_me(claims: dict = Depends(get_current_user)):
    return {"user_id": claims["sub"], "email": claims.get("email")}
```

---

## Revocation and Refresh Token Rotation

JWT access tokens are intentionally hard to revoke: if the signature, `iss`, `aud`, and `exp` are valid, the API can accept the token without calling the auth server. That statelessness is the feature and the tradeoff.

The usual production pattern, aligned with [RFC 9700 OAuth 2.0 Security BCP](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.14.2):

1. Keep access tokens short-lived: 5-30 minutes.
2. Issue refresh tokens as long-lived, server-tracked credentials.
3. Store only a hash of the refresh token.
4. Rotate the refresh token on every refresh.
5. If an old refresh token is reused, revoke the whole token family.

Why the family? Reuse means either the legitimate client retried with an old token or an attacker has a stolen token. The server cannot reliably know which one. Revoking the family stops the replay and forces a fresh login.

```python
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def refresh_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class RefreshTokenRecord:
    token_hash: str
    user_id: str
    family_id: str
    expires_at: datetime
    used_at: datetime | None = None
    revoked_at: datetime | None = None


async def rotate_refresh_token(raw_token: str) -> str:
    token_hash = refresh_token_hash(raw_token)
    record = await refresh_tokens.get_by_hash(token_hash)

    if record is None or record.revoked_at or record.expires_at <= datetime.now(timezone.utc):
        raise InvalidToken()

    if record.used_at is not None:
        # Reuse detection: assume the family is compromised.
        await refresh_tokens.revoke_family(record.family_id)
        raise InvalidToken()

    new_raw = new_refresh_token()
    new_record = RefreshTokenRecord(
        token_hash=refresh_token_hash(new_raw),
        user_id=record.user_id,
        family_id=record.family_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )

    await refresh_tokens.mark_used_and_insert(record.token_hash, new_record)
    return new_raw
```

This is the generic version of what managed IdPs call refresh-token rotation. If you use Cognito, Auth0, Okta, or another provider, prefer the provider's built-in rotation/revocation features and make your API validate the resulting access tokens.

---

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Decoding without verifying | Anyone can forge claims | Always verify signature first |
| Not checking `exp` | Accepting expired tokens | Include expiry check |
| Not checking `iss` | Accepting tokens from a different auth server | Always validate issuer |
| Not checking `aud` | Accepting tokens intended for a different service | Validate audience |
| Fetching JWKS per-request | Latency + external dependency per request | Cache JWKS with TTL |
| Putting secrets in claims | JWT payload is readable by anyone | Claims are not encrypted |
| Using symmetric keys (HS256) in multi-service setup | All services share the secret — anyone can forge tokens | Use asymmetric (RS256/ES256) |

---

## Debugging

```bash
# Decode a JWT in the terminal
echo "your.jwt.token" | cut -d. -f2 | base64 -d | python3 -m json.tool

# Or use the website
# https://jwt.io — paste any JWT, see decoded header and payload
```
