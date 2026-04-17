# OAuth 2.0 — The Authorization Framework

> OAuth 2.0 is a framework that lets a client obtain **limited access** to a resource on behalf of a user or itself — without sharing passwords.

---

## The Problem OAuth Solves

Before OAuth, if service B needed to access your data in service A, you gave service B your service A password. This is terrible:
- Service B has full access to everything, forever
- You can't revoke access to B without changing your password (which affects everything)
- If B is compromised, your password leaks

OAuth introduces **access tokens** — limited, scoped, expiring credentials. Service A (the authorization server) issues a token that says "this client can do X and Y, but not Z, until time T."

---

## Key Roles

| Role | What it is |
|------|-----------|
| **Resource Owner** | The entity that owns the data (usually a user) |
| **Client** | The application requesting access (your app or service) |
| **Authorization Server** | Issues tokens after authentication/authorization (Cognito, Auth0, Okta, etc.) |
| **Resource Server** | The API being protected (your API, or a third-party API) |

---

## Grant Types (Flows)

OAuth 2.0 defines several "grant types" — different ways for a client to get an access token. Use the right one for your situation.

### 1. Authorization Code (+ PKCE)

**Use for**: User-facing apps (web, mobile, SPA) where a human logs in.

```
User → Browser → Your App → Auth Server login page
                              ↓ user consents
Auth Server → Your App (callback with ?code=xxx)
Your App → Auth Server: exchange code for tokens
Auth Server → Your App: access_token + id_token + refresh_token
```

PKCE (Proof Key for Code Exchange) is now required for public clients (SPAs, mobile). It prevents auth code interception attacks.

```python
import secrets, hashlib, base64

# Client generates before redirect
code_verifier = secrets.token_urlsafe(64)
code_challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()
).rstrip(b"=").decode()

# Include in authorization URL
auth_url = (
    "https://auth.example.com/oauth2/authorize"
    f"?response_type=code"
    f"&client_id={client_id}"
    f"&redirect_uri={redirect_uri}"
    f"&scope=openid+email+profile"
    f"&code_challenge={code_challenge}"
    f"&code_challenge_method=S256"
)

# Exchange code for tokens (include verifier)
token_response = requests.post(token_url, data={
    "grant_type": "authorization_code",
    "code": received_code,
    "redirect_uri": redirect_uri,
    "client_id": client_id,
    "code_verifier": code_verifier,
})
```

### 2. Client Credentials

**Use for**: Machine-to-machine (M2M). No user is involved — your service authenticates directly to get a token.

```
Your service → Auth Server: POST with client_id + client_secret + scope
Auth Server → Your service: access_token (no refresh token)
Your service → Resource Server: Authorization: Bearer <access_token>
```

```python
import requests

token_response = requests.post(
    "https://auth.example.com/oauth2/token",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    data={
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "myapi/read myapi/write",
    },
)
access_token = token_response.json()["access_token"]
```

Key facts:
- No user, no user context — the token represents the **client/service itself**
- `client_secret` is required (must be kept server-side, never in frontend code)
- No refresh token — when it expires, just request a new one with client_id + secret
- The token's `sub` claim is the client ID, not a user ID

### 3. Refresh Token

**Use for**: Silently getting new access tokens without user re-authentication.

```
Your app stores refresh_token on login
Access token expires (typically 1 hour)
Your app → Auth Server: POST with refresh_token
Auth Server → Your app: new access_token (+ new id_token)
```

```python
token_response = requests.post(
    "https://auth.example.com/oauth2/token",
    data={
        "grant_type": "refresh_token",
        "refresh_token": stored_refresh_token,
        "client_id": client_id,
        # Confidential clients must also send client_secret (or use HTTP Basic auth).
        # Public clients (SPAs, mobile) omit it.
        # "client_secret": client_secret,
    },
)
new_access_token = token_response.json()["access_token"]
```

Refresh tokens have **no format defined by the spec** — treat them as opaque. Providers may issue random strings, encrypted blobs, or even JWTs internally, but clients should never try to parse them. They are long-lived (days to months) and must be stored securely.

### 4. Device Code (Device Authorization)

**Use for**: Devices without a browser or keyboard (smart TVs, CLIs, IoT).

```
Device → Auth Server: I need a code
Auth Server → Device: device_code, user_code, verification_uri
Device shows user: "Go to example.com/device and enter: ABCD-1234"
Device polls Auth Server every few seconds
User logs in on another device and enters the code
Auth Server → Device (on next poll): access_token
```

### 5. Resource Owner Password (Deprecated)

Sends username + password directly to the auth server. **Do not use** for new projects — it defeats the purpose of OAuth (in authorization code flow, the client never sees the password). Still appears in some legacy systems and automated tests.

Note: Cognito's `USER_PASSWORD_AUTH` / `ADMIN_USER_PASSWORD_AUTH` are **not** the OAuth 2.0 ROPC grant. They are Cognito-native auth flows exposed via the `InitiateAuth` / `AdminInitiateAuth` APIs — Cognito's `/oauth2/token` endpoint does not support `grant_type=password`.

---

## Scopes — Defining Permissions

Scopes are labels attached to tokens that express what the token is allowed to do.

### How Scopes Work

1. **Define scopes** on the auth server — e.g., `read`, `write`, `admin`
2. **Client requests specific scopes** when asking for a token
3. **Auth server issues a token** with only the approved scopes
4. **Your API checks scopes** before performing an action

```python
# Token payload (decoded)
{
    "scope": "myapi/read myapi/write",   # space-separated list
    "sub": "user-uuid",
    "exp": 1700003600
}

# Your API checks
def require_scope(required: str, claims: dict):
    scopes = claims.get("scope", "").split()
    if required not in scopes:
        raise HTTPException(status_code=403, detail=f"Missing scope: {required}")
```

### Standard OIDC Scopes

Built-in, user-identity related:

| Scope | What it grants |
|-------|---------------|
| `openid` | Required for OIDC. Enables ID token. Returns `sub` claim. |
| `email` | Returns `email` and `email_verified` claims |
| `profile` | Returns `name`, `family_name`, `given_name`, `picture`, etc. |
| `phone` | Returns `phone_number` and `phone_number_verified` |
| `address` | Returns `address` claim |
| `offline_access` | Issues a refresh token (not needed in all flows) |

### Custom Scopes (Resource Servers)

For your own APIs, define custom scopes on the auth server. The naming convention below (`<resource-server-id>/<scope>`) is **Cognito-specific** — Auth0 uses URL-style identifiers (`https://api.example.com/charge`), Okta uses simple strings, etc. OAuth 2.0 itself (RFC 6749) treats scopes as opaque space-separated strings with no required format.

```
# Cognito style
Resource Server ID: "payments-api"
Scope name:        "charge"
Full scope:        "payments-api/charge"
```

Whatever convention your provider uses, the prefix/identifier makes scopes unambiguous when one auth server protects multiple APIs.

---

## Resource Servers — Registering Your API

In RFC 6749, the "resource server" is simply the API that accepts access tokens — there is no registration concept in the core spec. The idea of **registering** a resource server (with an identifier + a set of declared scopes) on the auth server is a **product convention** used by Cognito, Auth0, and similar managed providers. It defines:
- A unique identifier (often a URL, sometimes a short name)
- The scopes your API recognizes

Why register? So the auth server knows which scopes are valid for which APIs. Clients can only request scopes that belong to a registered resource server.

```
Auth Server knows:
  Resource Server "payments-api":
    - payments-api/charge
    - payments-api/refund
  Resource Server "users-api":
    - users-api/read
    - users-api/delete

Client requests scope: "payments-api/charge users-api/read"
Auth Server issues token with exactly those scopes (if client is allowed)
```

---

## The Two Core Patterns

### Machine-to-Machine (Client Credentials)

```
┌──────────────┐   client_id + secret   ┌──────────────┐
│  Service A   │ ─────────────────────▶ │ Auth Server  │
│  (caller)    │ ◀── access_token ───── │              │
└──────────────┘                        └──────────────┘
       │
       │  Authorization: Bearer <token>
       ▼
┌──────────────┐
│  Service B   │  validates token → checks scope → responds
│  (your API)  │
└──────────────┘
```

**When**: Backend ↔ backend, cron jobs, data pipelines, any service-to-service call.
**Token contains**: client's identity, scopes, no user info.
**Refresh**: just request a new token with client_id + secret.

### User-Based (Authorization Code / Password)

```
┌───────────┐   login    ┌──────────────┐
│   User    │ ─────────▶ │ Auth Server  │
│ (browser) │ ◀─ tokens ─│              │
└───────────┘            └──────────────┘
      │
      │  Authorization: Bearer <access_token>
      ▼
┌───────────────┐
│  Your API     │  validates token → identifies user → responds
└───────────────┘
```

**When**: A human logs in and calls your API.
**Token contains**: user's identity (`sub`), user attributes, groups, scopes.
**Refresh**: use the refresh_token to silently get new tokens.

---

## Token Types

| Token | Format | Who reads it | Typical TTL |
|-------|--------|-------------|-------------|
| **Access Token** | JWT (Cognito, Auth0, Okta) or opaque (spec allows either) | Your API | 1-24 hours |
| **ID Token** | JWT (required by OIDC) | Your app (frontend/backend) | 1-24 hours |
| **Refresh Token** | No format defined by spec — treat as opaque | Auth server only | Days to months |

**Access token**: carry on every API request in `Authorization: Bearer <token>`. Your API validates it.

**ID token**: contains user profile information (name, email). For your app's UI layer to know who is logged in. **Don't use it to authorize API calls** — use the access token for that.

**Refresh token**: stored securely by your app. Used only to get new access/ID tokens. Never sent to your API.

---

## Comparison: Which Flow to Use

| Scenario | Flow | Notes |
|----------|------|-------|
| User logs into a web app | Authorization Code + PKCE | Standard for all user-facing apps |
| User logs into a mobile app | Authorization Code + PKCE | Same — PKCE is mandatory for mobile |
| Backend service calls another service | Client Credentials | No user context needed |
| CLI tool with no browser | Device Code | User authorizes on another device |
| Legacy app migration | Resource Owner Password | Avoid for new systems |
| Automated testing / scripts | Resource Owner Password or Client Credentials | OK for non-production |

---

## Related

- [jwt.md](./jwt.md) — JWT structure, JWKS, validation algorithm, Python code
- [cognito/oauth-jwt-guide.md](./cognito/oauth-jwt-guide.md) — Applying OAuth with AWS Cognito
