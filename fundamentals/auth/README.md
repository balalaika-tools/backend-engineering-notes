# Auth

> Authentication and authorization fundamentals + AWS Cognito in practice.

[![JWT](https://img.shields.io/badge/JWT-RFC7519-000000.svg?logo=jsonwebtokens&logoColor=white)](https://jwt.io)
[![OAuth2](https://img.shields.io/badge/OAuth2-RFC6749-EB5424.svg)](https://oauth.net/2/)
[![AWS Cognito](https://img.shields.io/badge/AWS_Cognito-latest-FF9900.svg?logo=amazonaws&logoColor=white)](https://aws.amazon.com/cognito/)
[![PyJWT](https://img.shields.io/badge/PyJWT-2.x-000000.svg)](https://pyjwt.readthedocs.io)

---

## Contents

| Guide | Layer | Reader outcome |
|---|---|---|
| [JWT](jwt.md) | Foundation | Validate a signed token against an explicit issuer, audience, algorithm, and freshness contract |
| [OAuth 2.0](oauth2.md) | Foundation | Choose an authorization flow and define scopes around the protected resource |
| [Cognito mental model](cognito/cognito.md) | AWS Cognito | Distinguish User Pools, Identity Pools, app clients, groups, and resource servers |
| [User Pool configuration](cognito/user-pool.md) | AWS Cognito | Configure clients, authentication flows, users, groups, and Lambda triggers |
| [Cognito tokens](cognito/tokens.md) | AWS Cognito | Select and validate the correct access or identity token and handle revocation limits |
| [OAuth and JWT in practice](cognito/oauth-jwt-guide.md) | AWS Cognito | Implement machine and user authorization flows through a FastAPI boundary |

---

## Learning path

**Working result by entry 2**: validate a token against an issuer contract, then explain how OAuth
delegates the authority represented by that token.

1. **Do:** [JWT validation](jwt.md) — pin the algorithm and validate issuer, audience, and freshness.
2. **Understand:** [OAuth 2.0](oauth2.md) — choose the authorization flow and scope boundary.
3. **Apply to Cognito:** [Cognito mental model](cognito/cognito.md), then [Cognito tokens](cognito/tokens.md).
4. **Integrate when needed:** [OAuth in Cognito](cognito/oauth-jwt-guide.md) and [User Pool configuration](cognito/user-pool.md).

**Stop here if** framework-neutral JWT validation and OAuth delegation answer the integration.
Continue into Cognito only when Cognito is the actual issuer or authorization server.

---

## Quick Decision Guide

| Question | Answer |
|----------|--------|
| What is a JWT and how does it work? | [jwt.md](./jwt.md) |
| How do I validate a JWT in Python? | [jwt.md → Python Utilities](./jwt.md) |
| What are OAuth flows / grant types? | [oauth2.md](./oauth2.md) |
| When to use client_credentials vs auth code? | [oauth2.md → The Two Core Patterns](./oauth2.md) |
| What are scopes and resource servers? | [oauth2.md → Scopes](./oauth2.md) |
| How do I set up Cognito for M2M auth? | [cognito/oauth-jwt-guide.md → Pattern A](./cognito/oauth-jwt-guide.md) |
| How do I set up Cognito for user login? | [cognito/oauth-jwt-guide.md → Pattern B](./cognito/oauth-jwt-guide.md) |
| IdToken vs AccessToken in Cognito? | [cognito/tokens.md](./cognito/tokens.md) |
| How do I create a user pool + app client? | [cognito/user-pool.md](./cognito/user-pool.md) |
| FastAPI + Cognito dependency | [cognito/oauth-jwt-guide.md → FastAPI Dependency](./cognito/oauth-jwt-guide.md) |

---

## Appendix — Sessions vs Tokens: Which One, When?

Most of this section is about **token-based** auth (JWTs + an auth header). The older model is **session cookies**: the server issues an opaque session ID, stores the session state in a DB or Redis, and the browser sends the cookie back on each request. Both are valid; the tradeoff is real. This table is for the "should we migrate from cookies to tokens" conversation.

| Dimension | Session cookies | Token-based (JWT) auth |
|-----------|-----------------|------------------------|
| **Revocation** | Instant — delete the row, user is logged out on next request | Not instant — tokens stay valid until `exp`. Need a deny-list for immediate cutoff (see [cognito/tokens.md § Token Revocation](./cognito/tokens.md#token-revocation)) |
| **Stateless servers** | No — session store is a required dependency | Yes — verify the signature, read claims |
| **Cross-domain** | Hard — cookies are scoped to a domain; third-party cookies are dying in browsers | Easy — Bearer token in the `Authorization` header works anywhere |
| **Mobile / CLI clients** | Awkward — needs cookie jar emulation | Natural — fetch a token, send as header |
| **XSS risk** | Lower — cookie can be `HttpOnly` and the JS can't read it | Higher — tokens are usually stored in localStorage or memory where JS can reach them |
| **CSRF risk** | Yes — cookies are sent automatically by the browser; use CSRF tokens and an appropriate `SameSite` policy | Depends on transport — an automatically sent token cookie remains CSRF-relevant; an `Authorization` header is not auto-attached, but a script-readable token increases cross-site scripting theft impact |
| **Size** | Small cookie (a session ID) | Larger header — JWTs are commonly 1–4 KB; matters on every request |
| **Downstream services** | Need to verify the session, usually via a central auth service | Self-contained — any service with the JWKS can verify |

### The rules of thumb

- **Web-only SaaS, single domain, can afford a session store**: sessions are simpler and more secure (HttpOnly, CSRF manageable). Don't fix what isn't broken.
- **Multi-client (web + mobile + API), multi-service, microservices**: tokens. The cross-domain and stateless-verification advantages outweigh the revocation pain.
- **"I need both"** — OIDC providers like Cognito let you do both: browsers get a session cookie at the auth server; backend APIs get a JWT. That's the common pattern for modern auth.
- **Session cookies + `SameSite=Strict` / `SameSite=Lax`** — with modern browser defaults (`SameSite=Lax` is default), CSRF risk is dramatically reduced. Don't dismiss sessions for CSRF fears alone.
- **Revocation latency is usually the deciding factor.** If your app requires "cancel this session now" (password reset forces logout, employee fired), tokens add friction (build a deny-list) that sessions give you for free.
