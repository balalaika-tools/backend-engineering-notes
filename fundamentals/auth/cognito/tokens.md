# Cognito Tokens — Deep Dive

> JWT structure, JWKS, and generic validation are covered in [auth/jwt.md](../jwt.md). This file covers what's Cognito-specific: the three token types, their claims, Cognito validation code, and revocation.

---

After a successful login, Cognito returns three tokens. Two (IdToken and AccessToken) are JWTs. The third (RefreshToken) is an encrypted opaque blob only Cognito can read.

---

## The Three Tokens

### IdToken — "Who is this user?"

Contains user identity information. Tells your app (and API) who the user is.

```json
{
  "sub": "a1b2c3d4-1234-5678-abcd-ef0123456789",
  "email": "jane@example.com",
  "email_verified": true,
  "name": "Jane Doe",
  "cognito:username": "jane@example.com",
  "cognito:groups": ["admins", "premium"],
  "custom:department": "engineering",
  "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_POOLID",
  "aud": "your-client-id",
  "token_use": "id",
  "auth_time": 1700000000,
  "iat": 1700000000,
  "exp": 1700003600
}
```

Key claims:
- `sub` — the user's permanent UUID. **Always use this as your user's primary key.** Email can change, `sub` never does.
- `email`, `name`, etc. — standard user attributes
- `cognito:groups` — which groups the user is in
- `custom:*` — your custom attributes
- `aud` — must match your `client_id`
- `token_use` — must be `"id"`
- `exp` — expiry timestamp (Unix)

### AccessToken — "What can this user do?"

Used to authorize API calls. Less about identity, more about permissions and scopes.

```json
{
  "sub": "a1b2c3d4-1234-5678-abcd-ef0123456789",
  "cognito:groups": ["admins", "premium"],
  "scope": "openid email profile",
  "client_id": "your-client-id",
  "username": "jane@example.com",
  "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_POOLID",
  "token_use": "access",
  "auth_time": 1700000000,
  "iat": 1700000000,
  "exp": 1700003600,
  "jti": "unique-token-id"
}
```

Key differences from IdToken:
- Has `scope` instead of user attributes
- `client_id` is always present; `aud` is absent for plain app-client tokens, but **is** set to the resource-server identifier when the token is issued for a resource server (e.g. `api.myapp.com`)
- `token_use` is `"access"`
- No PII (email, name) — just authorization data

**Which token should your API validate?**
- Use **AccessToken** to authorize API calls — that's its purpose
- Use **IdToken** only if your API needs user profile info (and you're not using a separate user lookup)

### RefreshToken — "Get me new tokens"

An encrypted, opaque string (not a JWT). Only Cognito can read it. Your app stores it and uses it to silently get new Access/Id tokens when they expire.

```python
response = cognito_client.initiate_auth(
    ClientId=client_id,
    AuthFlow='REFRESH_TOKEN_AUTH',
    AuthParameters={'REFRESH_TOKEN': stored_refresh_token},
)

new_tokens = response['AuthenticationResult']
# new_tokens['AccessToken'], new_tokens['IdToken']
# By default a new RefreshToken is NOT issued — the same one keeps working until it
# expires or is revoked. If you opt into refresh token rotation (see below), a fresh
# RefreshToken comes back on each refresh and the old one is invalidated.
```

---

## Default Token Lifetimes

| Token | Default | Min | Max | Configurable per App Client |
|-------|---------|-----|-----|----------------------------|
| AccessToken | 1 hour | 5 min | 24 hours | Yes |
| IdToken | 1 hour | 5 min | 24 hours | Yes |
| RefreshToken | 30 days | 1 hour | 10 years | Yes |

Set them per App Client during creation or update:
```python
cognito_client.update_user_pool_client(
    UserPoolId=pool_id,
    ClientId=client_id,
    AccessTokenValidity=15,
    IdTokenValidity=15,
    RefreshTokenValidity=7,
    TokenValidityUnits={
        'AccessToken': 'minutes',
        'IdToken': 'minutes',
        'RefreshToken': 'days',
    },
)
```

---

## Token Validation (In Your API)

**Never trust a JWT without validating its signature.** Anyone can create a JWT — the signature is what proves Cognito issued it.

### How Validation Works

Cognito signs tokens with a private RSA key. Your API validates the signature using Cognito's public key (published at a well-known URL called the JWKS endpoint).

```
JWKS endpoint:
https://cognito-idp.<region>.amazonaws.com/<pool_id>/.well-known/jwks.json
```

**Start with the generic JWT validation algorithm in [auth/jwt.md §Validation Algorithm](../jwt.md#validation-algorithm).** Cognito adds three extra checks on top:

| Step | Generic JWT | Cognito addition |
|------|-------------|------------------|
| `iss` | match your auth server | must be `https://cognito-idp.<region>.amazonaws.com/<pool_id>` exactly |
| `aud` vs `client_id` | check whichever your issuer sets | **IdToken** → check `aud`; **AccessToken** → check `client_id` (Cognito access tokens typically have no `aud` unless resource-server-scoped) |
| `token_use` | n/a | must be `"id"` or `"access"` — reject if the wrong kind shows up where the other is expected |

### Python Validation

```python
import boto3
import json
import time
import urllib.request
from jose import jwk, jwt
from jose.utils import base64url_decode

REGION = 'us-east-1'
POOL_ID = 'us-east-1_XXXXXXXX'
CLIENT_ID = 'your-client-id'

JWKS_URL = f'https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}/.well-known/jwks.json'

# Cache this. See auth/jwt.md §"JWKS" for cache-TTL and rotation guidance —
# the short version: cache for 15–60 minutes and refetch on `kid` miss.
_jwks_cache: dict | None = None


def get_jwks(force_refresh: bool = False) -> dict:
    global _jwks_cache
    if _jwks_cache is None or force_refresh:
        with urllib.request.urlopen(JWKS_URL) as f:
            _jwks_cache = json.loads(f.read())
    return _jwks_cache


def _find_key(jwks: dict, kid: str) -> dict | None:
    return next((k for k in jwks['keys'] if k['kid'] == kid), None)


def validate_token(token: str, token_use: str = 'access') -> dict:
    """
    Validate a Cognito JWT. Returns the decoded claims if valid.
    Raises an exception if invalid.
    token_use: 'access' or 'id'
    """
    # Decode header without verification to get kid
    headers = jwt.get_unverified_headers(token)
    kid = headers['kid']

    # Find the matching public key. On `kid` miss, refetch JWKS once and retry —
    # Cognito rotates keys, and a miss usually means the cache is stale, not that
    # the token is bad. Only give up after the refetch also fails.
    jwks = get_jwks()
    key = _find_key(jwks, kid)
    if not key:
        jwks = get_jwks(force_refresh=True)
        key = _find_key(jwks, kid)
    if not key:
        raise ValueError("Public key not found after JWKS refresh — token may be from a different pool")

    # Verify signature and decode
    public_key = jwk.construct(key)
    message, encoded_sig = token.rsplit('.', 1)
    decoded_sig = base64url_decode(encoded_sig.encode())

    if not public_key.verify(message.encode(), decoded_sig):
        raise ValueError("Signature verification failed")

    # Decode claims
    claims = jwt.get_unverified_claims(token)

    # Validate expiry
    if claims['exp'] < time.time():
        raise ValueError("Token is expired")

    # Validate issuer
    expected_iss = f'https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}'
    if claims['iss'] != expected_iss:
        raise ValueError("Invalid issuer")

    # Validate token use
    if claims['token_use'] != token_use:
        raise ValueError(f"Expected token_use={token_use}, got {claims['token_use']}")

    # Validate audience (id token) or client_id (access token)
    if token_use == 'id' and claims.get('aud') != CLIENT_ID:
        raise ValueError("Invalid audience")
    if token_use == 'access' and claims.get('client_id') != CLIENT_ID:
        raise ValueError("Invalid client_id")

    return claims


# Usage in your API
def protected_endpoint(authorization_header: str):
    token = authorization_header.replace('Bearer ', '')
    claims = validate_token(token, token_use='access')

    user_sub = claims['sub']
    groups   = claims.get('cognito:groups', [])

    if 'admins' not in groups:
        raise PermissionError("Admins only")

    return {"user": user_sub, "groups": groups}
```

### Using PyJWT (alternative library)

```python
import jwt
import requests

JWKS_URL = f'https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}/.well-known/jwks.json'

jwks_client = jwt.PyJWKClient(JWKS_URL)

def validate_token_pyjwt(token: str) -> dict:
    signing_key = jwks_client.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=['RS256'],
        audience=CLIENT_ID,         # only for id tokens; omit for access tokens
        options={"verify_aud": False},  # set True for id tokens
    )
```

---

## API Gateway Integration (Skip Manual Validation)

If your API runs behind API Gateway, you can configure a **Cognito User Pool Authorizer** and API Gateway validates the token for you — no code needed.

```python
apigw = boto3.client('apigateway')

# Create authorizer
response = apigw.create_authorizer(
    restApiId='your-api-id',
    name='CognitoAuthorizer',
    type='COGNITO_USER_POOLS',
    providerARNs=[f'arn:aws:cognito-idp:{REGION}:{ACCOUNT_ID}:userpool/{POOL_ID}'],
    identitySource='method.request.header.Authorization',
)

# API Gateway will:
# 1. Extract the token from the Authorization header
# 2. Validate it against the User Pool
# 3. Pass claims to your Lambda as event['requestContext']['authorizer']['claims']
# 4. Return 401 automatically if invalid
```

Lambda receives:
```python
def handler(event, context):
    claims = event['requestContext']['authorizer']['claims']
    user_sub = claims['sub']
    email    = claims['email']
    groups   = claims.get('cognito:groups', '').split(',')
```

---

## Token Revocation

By default, tokens are stateless — Cognito can't invalidate them before expiry. But you can enable revocation.

**Revoke a user's refresh token (logs them out everywhere):**
```python
# Get the client secret if the client has one
cognito_client.revoke_token(
    Token=refresh_token,
    ClientId=client_id,
    # ClientSecret=client_secret,  # include if client has a secret
)
# This invalidates the refresh token.
# Existing access/id tokens remain valid until their expiry (up to 1 hour).
```

**Force sign-out everywhere (admin):**
```python
cognito_client.admin_user_global_sign_out(
    UserPoolId=pool_id,
    Username='jane@example.com',
)
# Invalidates all of the user's refresh tokens and revokes access/id tokens
# for calls made against Cognito's own APIs (e.g. GetUser, UpdateUserAttributes).
# Access/id tokens presented to *your* backend keep passing JWT signature checks
# until they expire — your API must check a deny-list (or the jti against a revocation store)
# if you need immediate cutoff for downstream APIs.
```

**Enable token revocation on a client** (this is the default for app clients created since mid-2021, but worth confirming on older pools):
```python
cognito_client.update_user_pool_client(
    UserPoolId=pool_id,
    ClientId=client_id,
    EnableTokenRevocation=True,
)
```

### Refresh Token Rotation (Opt-In, since April 2025)

By default Cognito reuses the same refresh token until it expires. You can opt into
**rotation** so each refresh returns a brand-new refresh token and invalidates the old
one — a defense against refresh-token replay that lets you safely use longer refresh TTLs.

```python
cognito_client.update_user_pool_client(
    UserPoolId=pool_id,
    ClientId=client_id,
    RefreshTokenRotation={
        'Feature': 'ENABLED',
        'RetryGracePeriodSeconds': 30,   # old token still works briefly for client retries (max 60)
    },
)
# When enabled, refresh via GetTokensFromRefreshToken or the /oauth2/token endpoint —
# REFRESH_TOKEN_AUTH on initiate_auth is not compatible with rotation.
# Requires the Essentials or Plus pricing tier.
```

---

## Quick Reference

```
On login:
  IdToken     → decode to get user info (email, name, groups, custom attrs)
  AccessToken → send in Authorization header to your API
  RefreshToken → store securely, use to get new tokens when AccessToken expires

On every API request:
  Authorization: Bearer <AccessToken>

When AccessToken expires (after 1 hour by default):
  Call initiate_auth with REFRESH_TOKEN_AUTH → get new AccessToken + IdToken

When RefreshToken expires (after 30 days by default):
  User must log in again

The only token your API should validate:
  AccessToken (use IdToken only if you genuinely need user attributes in the request)
```

---

## Related Docs

- [cognito.md](./cognito.md) — Mental model and overview
- [user-pool.md](./user-pool.md) — User pool, app clients, auth flows, boto3
