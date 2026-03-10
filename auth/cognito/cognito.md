# Amazon Cognito — The Mental Model

## What Problem Does It Solve?

Every app needs to answer two questions:
1. **Who is this user?** (authentication)
2. **What can they do?** (authorization)

Building this yourself means: a user database, password hashing, email verification, MFA, token generation, token validation, session management, forgot-password flows, brute-force protection... Cognito handles all of that.

---

## The Two Services

Cognito has two completely separate services that solve different problems. Don't confuse them.

### User Pools — "Who is this user?"

A **User Pool** is a managed user directory + authentication engine. It stores your users and handles logging them in.

Think of it as: *your own private auth system running on AWS*.

When a user successfully logs in, the User Pool issues **JWT tokens** (cryptographically signed JSON). Your app and your API use these tokens to know who the user is and what they're allowed to do.

### Identity Pools — "What AWS services can this user touch directly?"

An **Identity Pool** takes a token (from a User Pool, Google, Facebook, SAML, etc.) and exchanges it for **temporary AWS credentials** (via STS).

Use this only when your frontend needs to call AWS services directly — like uploading to S3 from a browser. If all your AWS calls go through your own backend, you don't need an Identity Pool.

**In most web/mobile apps, you only need a User Pool.**

---

## Two Kinds of Policies — The Common Confusion

People often wonder: *"If my user has permissions, doesn't AWS need to know about them somewhere?"*

Not necessarily. There are two completely separate places where policies live:

### 1) App policies / business rules (no Identity Pool needed)

Examples: admin sees all invoices, user sees only their own, manager can approve requests.

These live in your **backend code**, not in AWS IAM. The backend reads the User Pool JWT and applies its own logic:

```
Frontend  →  JWT in Authorization header  →  Your Backend
                                               ↓
                                          validate token
                                          read cognito:groups or other claims
                                          apply your own rules
                                          talk to S3/DynamoDB with the backend's own IAM role
```

The user never has AWS credentials — only a JWT. The backend has the IAM role. This is **"User Pool for auth, backend for authorization"** — the standard pattern.

```python
claims = validate_token(token)
groups = claims.get("cognito:groups", [])

if "admin" in groups:
    # show all invoices
elif claims["sub"] == requested_user_id:
    # show only their own
else:
    raise PermissionError("Forbidden")
```

### 2) IAM policies for direct AWS resource access (needs Identity Pool)

Examples: browser uploads directly to S3, frontend reads DynamoDB directly without a backend.

Here the user needs actual AWS credentials. The Identity Pool exchanges the User Pool JWT for **temporary AWS credentials** (via STS). Those credentials carry IAM policies that govern what AWS resources the user can touch directly.

```
Frontend  →  User Pool JWT  →  Identity Pool  →  STS  →  temporary AWS credentials
                                                              ↓
                                                     frontend calls S3 directly with those creds
```

### The bottom line

| Scenario | Where policies live | Needs Identity Pool? |
|----------|--------------------|--------------------|
| Backend enforces what the user can do | JWT claims + your code | No |
| Frontend calls AWS services directly | IAM policies via STS | Yes |

If all AWS calls go through your backend, the user never needs IAM credentials. Your backend reads the JWT, decides what's allowed, and uses **its own** IAM role to talk to AWS.

---

## How a User Pool Works — The Full Picture

```
┌─────────────┐         ┌──────────────────────────────────────┐
│   Your App  │         │           User Pool                  │
│             │         │                                      │
│  Login form │──────▶  │  App Client (client_id)              │
│             │         │  ├── Which auth flows are allowed?   │
│             │         │  ├── Token TTLs                      │
│             │         │  └── OAuth scopes                    │
│             │         │                                      │
│             │         │  Users directory                     │
│             │         │  ├── jane@example.com                │
│             │         │  ├── john@example.com                │
│             │         │  └── ...                             │
│             │◀──────  │                                      │
│  JWT tokens │         │  Returns: IdToken, AccessToken,      │
│             │         │           RefreshToken               │
└─────────────┘         └──────────────────────────────────────┘
       │
       │  Bearer <AccessToken>
       ▼
┌─────────────┐
│   Your API  │  validates JWT signature → knows who the user is
└─────────────┘
```

**Step by step:**
1. User enters email + password in your app
2. Your app calls Cognito with: `client_id` + credentials
3. Cognito checks: valid client? valid credentials? MFA required?
4. Cognito returns three JWT tokens
5. Your app stores these tokens (memory, cookies, secure storage)
6. On every API request, your app sends: `Authorization: Bearer <AccessToken>`
7. Your API validates the token's signature against Cognito's public keys
8. When the AccessToken expires (default: 1 hour), your app silently uses the RefreshToken to get new tokens — user never notices

---

## The Key Components

### User Pool
The container. Holds your users and all configuration for how authentication works. You have one (or a few) user pools. All users of your application live here.

**Pool-level settings:**
- Password policies
- MFA requirements
- Email/SMS verification
- Account recovery flows
- Lambda triggers
- Which attributes users have (email, name, custom fields)

### App Client
**This is the part people find confusing.**

An App Client is a registration of one of your applications with the User Pool. It answers: *which app is making this login request, and what is that app allowed to do?*

Every login request includes a `client_id`. Cognito looks up that client_id to know which App Client configuration to apply.

**Why does this exist?**

One User Pool, multiple apps. Example:
- Your **web app** — allows social login (Google, Facebook), uses authorization code flow, tokens expire in 1 hour
- Your **mobile app** — only allows SRP auth (no social login), tokens expire in 30 days
- Your **internal admin tool** — requires MFA, shorter token lifetime, different OAuth scopes
- Your **backend service** — uses client credentials (machine-to-machine, no user login at all)

All four share the **same user database** (User Pool) but each has its own App Client with different rules.

**App Client has:**
- `client_id` — always present, identifies the client
- `client_secret` — optional, only for server-side apps (never put in browser/mobile code)
- Which auth flows are enabled (`USER_SRP_AUTH`, `USER_PASSWORD_AUTH`, `REFRESH_TOKEN_AUTH`, etc.)
- Token validity durations (AccessToken, IdToken, RefreshToken)
- OAuth scopes it can request
- Which attributes it can read/write

> Rule of thumb: **no client secret for browser or mobile apps** (it would be exposed). Client secret only for server-side code.

### Groups
Optional. You can put users in groups (e.g. `admins`, `premium`, `free-tier`). Group membership is included in the JWT tokens as the `cognito:groups` claim. Your backend reads that claim to make authorization decisions.

```python
# In your API, after decoding the JWT:
groups = decoded_token.get('cognito:groups', [])
if 'admins' not in groups:
    raise PermissionError("Admin only")
```

### Lambda Triggers
Hooks that fire at specific points in the auth lifecycle. Let you customize behavior without forking Cognito. See [user-pool.md](./user-pool.md) for the full list.

---

## The Tokens

Three tokens come back on successful login:

| Token | What it is | What it's for | Default TTL |
|-------|-----------|---------------|-------------|
| **IdToken** | JWT with user info (email, name, groups) | "Who is this user?" — identity | 1 hour |
| **AccessToken** | JWT with scopes and permissions | Authorize API calls | 1 hour |
| **RefreshToken** | Encrypted opaque string | Get new Id/Access tokens silently | 30 days |

See [tokens.md](./tokens.md) for deep detail on JWT structure, validation, and the refresh flow.

---

## What You Build vs What Cognito Handles

| You build | Cognito handles |
|-----------|----------------|
| Login UI (form, buttons) | Password hashing and storage |
| Sending tokens on API requests | Email/SMS verification codes |
| Validating tokens in your API | MFA flows |
| Authorization logic (what can this user do) | Brute-force lockout |
| | Account recovery flows |
| | Token signing and expiry |
| | Social login OAuth handshake |
| | SAML federation |

---

## Files in This Folder

| File | What's in it |
|------|-------------|
| `cognito.md` | This file — mental model and how pieces connect |
| `user-pool.md` | User pool setup, app clients, auth flows, groups, Lambda triggers, boto3 |
| `tokens.md` | Cognito token claims (IdToken vs AccessToken), Cognito-specific validation, revocation |
| `oauth-jwt-guide.md` | Applying OAuth with Cognito — resource servers, client credentials, user auth, FastAPI integration |

For JWT theory and OAuth concepts, see the parent folder:

| File | What's in it |
|------|-------------|
| [../jwt.md](../jwt.md) | JWT structure, JWKS, generic validation algorithm, Python utilities |
| [../oauth2.md](../oauth2.md) | OAuth 2.0 grant types, scopes, resource servers — framework-agnostic |
