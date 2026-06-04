# OAuth & JWT with Cognito — Practical Guide

> Theory is in [auth/jwt.md](../jwt.md) and [auth/oauth2.md](../oauth2.md). This guide shows how to apply those concepts specifically with AWS Cognito — boto3 setup, token flow, real code.

---

## Cognito Resource Servers — Defining Custom Scopes

A **Resource Server** in Cognito registers your API and defines the custom scopes it recognizes. Without it, you can only use the built-in OIDC scopes (`openid`, `email`, `profile`).

```python
import boto3

cognito = boto3.client("cognito-idp", region_name="us-east-1")

cognito.create_resource_server(
    UserPoolId=pool_id,
    Identifier="myapi",           # becomes the scope prefix: myapi/read, myapi/write
    Name="My Application API",
    Scopes=[
        {"ScopeName": "read",  "ScopeDescription": "Read access"},
        {"ScopeName": "write", "ScopeDescription": "Read and write access"},
        {"ScopeName": "admin", "ScopeDescription": "Full administrative access"},
    ],
)
# Scopes are now available: "myapi/read", "myapi/write", "myapi/admin"
```

Custom scopes follow the format `<Identifier>/<ScopeName>`. The identifier is the resource server's unique ID (not a URL, just a string — though URLs are valid too).

---

## Pattern A — Machine-to-Machine (Client Credentials)

No user involved. A service authenticates with its own client_id + secret to call your API.

See [oauth2.md → Client Credentials](../oauth2.md) for the flow diagram.

### Step 1: Create an App Client for M2M

```python
client_resp = cognito.create_user_pool_client(
    UserPoolId=pool_id,
    ClientName="backend-service",
    GenerateSecret=True,                              # required for client_credentials
    AllowedOAuthFlows=["client_credentials"],
    AllowedOAuthScopes=["myapi/read", "myapi/write"], # custom scopes from resource server
    AllowedOAuthFlowsUserPoolClient=True,
    SupportedIdentityProviders=["COGNITO"],
    ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"],
)
client_id     = client_resp["UserPoolClient"]["ClientId"]
client_secret = client_resp["UserPoolClient"]["ClientSecret"]
```

**Requirements for client_credentials in Cognito:**
- `GenerateSecret=True` — the secret is how the service proves its identity
- A **User Pool Domain** must exist (the token endpoint is served there)
- Resource Server with custom scopes must exist before adding scopes here

### Step 2: Add a User Pool Domain (if not already)

```python
cognito.create_user_pool_domain(
    Domain="myapp-auth",   # becomes: myapp-auth.auth.us-east-1.amazoncognito.com
    UserPoolId=pool_id,
)
```

### Step 3: Request a Token

```python
import requests

REGION = "us-east-1"
DOMAIN_PREFIX = "myapp-auth"

token_url = f"https://{DOMAIN_PREFIX}.auth.{REGION}.amazoncognito.com/oauth2/token"

resp = requests.post(
    token_url,
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    data={
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "myapi/read myapi/write",
    },
)
resp.raise_for_status()
access_token = resp.json()["access_token"]
```

### Step 4: Call Your API

```python
api_response = requests.get(
    "https://your-api.example.com/data",
    headers={"Authorization": f"Bearer {access_token}"},
)
```

### Key Facts About client_credentials Tokens

- `sub` in the token = the `client_id` (not a user UUID)
- Only an `access_token` is returned — no id_token, no refresh_token
- When the token expires, just POST to the token endpoint again with client_id + secret
- No user pool users required — the app client IS the identity

---

## Pattern B — User Authentication (USER_PASSWORD_AUTH)

A human user authenticates. The token represents a specific person.

See [oauth2.md → User-Based](../oauth2.md) for the flow diagram.

### Step 1: Create a User Pool Client for User Auth

```python
client_resp = cognito.create_user_pool_client(
    UserPoolId=pool_id,
    ClientName="web-app",
    GenerateSecret=False,                    # no secret for browser/mobile apps
    ExplicitAuthFlows=[
        "ALLOW_USER_PASSWORD_AUTH",          # plain password over HTTPS (server-side only)
        "ALLOW_REFRESH_TOKEN_AUTH",
    ],
    AccessTokenValidity=60,
    IdTokenValidity=60,
    RefreshTokenValidity=30,
    TokenValidityUnits={
        "AccessToken": "minutes",
        "IdToken": "minutes",
        "RefreshToken": "days",
    },
)
client_id = client_resp["UserPoolClient"]["ClientId"]
```

For browser/mobile apps, use `ALLOW_USER_SRP_AUTH` instead of `ALLOW_USER_PASSWORD_AUTH` — SRP means the password never travels the wire. For server-side scripts and tests, `USER_PASSWORD_AUTH` is fine.

### Step 2: Create a User

```python
cognito.admin_create_user(
    UserPoolId=pool_id,
    Username="alice@example.com",
    UserAttributes=[{"Name": "email", "Value": "alice@example.com"}],
    TemporaryPassword="Temp1234!",
    MessageAction="SUPPRESS",
)
cognito.admin_set_user_password(
    UserPoolId=pool_id,
    Username="alice@example.com",
    Password="MyPerm1234!",
    Permanent=True,
)
```

### Step 3: Authenticate

```python
auth_resp = cognito.initiate_auth(
    ClientId=client_id,
    AuthFlow="USER_PASSWORD_AUTH",
    AuthParameters={
        "USERNAME": "alice@example.com",
        "PASSWORD": "MyPerm1234!",
    },
)
result       = auth_resp["AuthenticationResult"]
access_token  = result["AccessToken"]
id_token      = result["IdToken"]
refresh_token = result["RefreshToken"]
```

### Step 4: Use and Refresh Tokens

```python
# Call your API
requests.get("https://your-api.example.com/me", headers={"Authorization": f"Bearer {access_token}"})

# Refresh when access token expires
refresh_resp = cognito.initiate_auth(
    ClientId=client_id,
    AuthFlow="REFRESH_TOKEN_AUTH",
    AuthParameters={"REFRESH_TOKEN": refresh_token},
)
new_access_token = refresh_resp["AuthenticationResult"]["AccessToken"]
new_id_token     = refresh_resp["AuthenticationResult"]["IdToken"]
# Refresh token is NOT renewed — use the same one until it expires (30 days default)
```

---

## Validating Tokens in Your API (Cognito-Specific)

Cognito's JWKS and issuer URLs follow a fixed pattern. Plug these into the generic validation from [jwt.md](../jwt.md).

```python
REGION  = "us-east-1"
POOL_ID = "us-east-1_XXXXXXXX"

JWKS_URL = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}/.well-known/jwks.json"
ISSUER   = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"
```

### Checking Scopes (M2M / client_credentials)

```python
def require_scope(required_scope: str, claims: dict) -> None:
    scopes = claims.get("scope", "").split()
    if required_scope not in scopes:
        raise HTTPException(status_code=403, detail=f"Missing scope: {required_scope}")

# Usage
claims = validate_token(token)
require_scope("myapi/write", claims)
```

### Checking Groups (user auth)

```python
def require_group(required_group: str, claims: dict) -> None:
    groups = claims.get("cognito:groups", [])
    if required_group not in groups:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

# Usage
claims = validate_token(token)
require_group("admins", claims)
```

### FastAPI Dependency (Cognito)

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient

REGION  = "us-east-1"
POOL_ID = "us-east-1_XXXXXXXX"

jwks_client = PyJWKClient(
    f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}/.well-known/jwks.json"
)
ISSUER    = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"
CLIENT_ID = "your-client-id"

bearer_scheme = HTTPBearer()


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
            options={"verify_aud": False},  # verify manually below — aud location differs per token type
        )
        # IdToken puts the app client in `aud`. AccessToken uses `client_id` (not `aud`)
        # to identify the app client. `aud` appears on access tokens only when the token was
        # issued for a resource-server-scoped request; it then holds the resource server identifier,
        # not the app client ID — so the aud-equals-CLIENT_ID comparison below is a no-op on
        # resource-server access tokens and should not be relied on for those.
        if claims.get("client_id") != CLIENT_ID and claims.get("aud") != CLIENT_ID:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid client")
        return claims
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
```

---

## Consuming Cognito Tokens at the Gateway (API Gateway / AgentCore JWT Authorizer)

`CustomJWTAuthorizer` is an **API Gateway / AgentCore** construct, not a Cognito feature — Cognito only issues the token. When you configure an API Gateway or AgentCore Gateway to protect an endpoint, you point it at Cognito's discovery URL and list the client IDs it should trust:

```python
auth_config = {
    "customJWTAuthorizer": {
        "allowedClients": [client_id],
        "discoveryUrl": f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}/.well-known/openid-configuration",
    }
}
```

The gateway (not Cognito):
1. Fetches the discovery document to find `jwks_uri` and `issuer`
2. Downloads and caches the Cognito public keys
3. Validates every incoming JWT signature and claims
4. Rejects tokens from clients not in `allowedClients`

You write no validation code — you only configure which Cognito pool and clients the gateway should trust. Your origin service behind the gateway can then trust the request has already been authenticated.

---

## Production vs Testing — Where the Token Comes From

### Production: Real Token Flow

```
Browser → Cognito Hosted UI (or Amplify SDK) → user logs in
       → tokens returned to frontend
       → frontend sends access_token to YOUR backend
       → backend validates JWT
       → backend calls downstream services with Bearer token
```

The frontend never calls downstream services directly. Your backend is the gatekeeper.

```
WRONG: Browser ──────────────────── Bearer token ──────────────▶ AgentCore
RIGHT: Browser ── JWT ──▶ Your Backend ── Bearer token ──▶ AgentCore
```

### Testing: The Shortcut

In scripts and notebooks, skip the browser flow:

```python
# Option 1: call Cognito directly (hardcoded credentials — test environments only)
auth_resp = cognito.initiate_auth(
    ClientId=client_id,
    AuthFlow="USER_PASSWORD_AUTH",
    AuthParameters={"USERNAME": "testuser", "PASSWORD": "TestPass123!"},
)
bearer_token = auth_resp["AuthenticationResult"]["AccessToken"]

# Option 2: for M2M, just use client credentials (valid in any environment)
token_resp = requests.post(token_url, data={
    "grant_type": "client_credentials",
    "client_id": client_id,
    "client_secret": client_secret,
    "scope": "myapi/read",
})
bearer_token = token_resp.json()["access_token"]

# Use directly — same JWT format, same validation
response = requests.get(api_url, headers={"Authorization": f"Bearer {bearer_token}"})
```

The token is the same JWT either way. The downstream service only cares about the signature — not how you obtained it.

---

## Token Refresh Helper

The generic `decode_jwt_payload` / `is_token_expired` / `seconds_until_expiry` utilities live in [auth/jwt.md §Python Utilities](../jwt.md#python-utilities) — use those. The Cognito-specific part is just the refresh call:

```python
import boto3
from fundamentals.auth.jwt_utils import is_token_expired  # the generic helper from jwt.md


def get_valid_access_token(
    access_token: str,
    refresh_token: str,
    client_id: str,
    region: str = "us-east-1",
) -> str:
    """Return a valid access token, refreshing via Cognito if it expires within 5 minutes."""
    if not is_token_expired(access_token, buffer_seconds=300):
        return access_token

    cognito = boto3.client("cognito-idp", region_name=region)
    resp = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow="REFRESH_TOKEN_AUTH",
        AuthParameters={"REFRESH_TOKEN": refresh_token},
    )
    return resp["AuthenticationResult"]["AccessToken"]
```

> **By default, Cognito does not rotate refresh tokens** — the same refresh token keeps working until it expires (30 days by default) or is explicitly revoked. Since April 2025, refresh token rotation is an opt-in feature: enable it on the app client (`RefreshTokenRotation={'Feature': 'ENABLED'}`, Essentials/Plus tiers), and each refresh call returns a *new* refresh token while invalidating the old one (with a short grace period, up to 60s, for client retries). Rotation requires refreshing via `GetTokensFromRefreshToken` / the `/oauth2/token` endpoint rather than `REFRESH_TOKEN_AUTH`. Other IdPs rotate by default — the security tradeoff is discussed in [oauth2.md](../oauth2.md).

---

## Comparison: M2M vs User Auth

| Aspect | M2M (client_credentials) | User Auth |
|--------|--------------------------|-----------|
| Who authenticates | Service/script | Human user |
| Needs client secret | Yes | No (public clients) |
| Needs Resource Server | Yes (custom scopes) | No (unless you add custom scopes) |
| Needs User Pool Domain | Yes (token endpoint) | No (`initiate_auth` API) |
| Tokens returned | AccessToken only | AccessToken + IdToken + RefreshToken |
| Token `sub` | The client_id | The user's UUID |
| Refresh | Re-request with client_id + secret | Use RefreshToken |
| User accounts in pool | Not needed | Yes |

---

## Related

- [jwt.md](../jwt.md) — JWT structure, JWKS, validation algorithm, Python code
- [oauth2.md](../oauth2.md) — OAuth 2.0 grant types, scopes, resource servers
- [cognito.md](./cognito.md) — Cognito mental model (User Pools vs Identity Pools)
- [user-pool.md](./user-pool.md) — User pool setup, app clients, auth flows, groups
- [tokens.md](./tokens.md) — Cognito token claims, IdToken vs AccessToken, revocation
