# Auth

> Authentication and authorization fundamentals + AWS Cognito in practice.

[![JWT](https://img.shields.io/badge/JWT-RFC7519-000000.svg?logo=jsonwebtokens&logoColor=white)](https://jwt.io)
[![OAuth2](https://img.shields.io/badge/OAuth2-RFC6749-EB5424.svg)](https://oauth.net/2/)
[![AWS Cognito](https://img.shields.io/badge/AWS_Cognito-latest-FF9900.svg?logo=amazonaws&logoColor=white)](https://aws.amazon.com/cognito/)
[![python-jose](https://img.shields.io/badge/python--jose-3.x-4B8BBE.svg)](https://github.com/mpdavis/python-jose)

---

## Structure

```
auth/
├── jwt.md         ← JWT theory: structure, JWKS, validation, Python code
├── oauth2.md      ← OAuth 2.0: grant types, scopes, resource servers
└── cognito/       ← AWS Cognito — practical application of the above
    ├── cognito.md            ← Mental model: User Pools vs Identity Pools
    ├── user-pool.md          ← Setup, app clients, auth flows, groups, boto3
    ├── tokens.md             ← Cognito token claims, validation, revocation
    └── oauth-jwt-guide.md    ← OAuth in Cognito: M2M, user auth, FastAPI, testing
```

**Read the base layer first, then the framework layer.**

| Base (framework-agnostic) | Cognito-specific |
|---------------------------|------------------|
| [jwt.md](./jwt.md) | [cognito/tokens.md](./cognito/tokens.md) |
| [oauth2.md](./oauth2.md) | [cognito/oauth-jwt-guide.md](./cognito/oauth-jwt-guide.md) |

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
