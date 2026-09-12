"""Application-facing bearer-token verification contract."""

from dataclasses import dataclass
from typing import Protocol


class TokenVerificationError(RuntimeError):
    """Base failure raised by a token verifier."""


class InvalidTokenError(TokenVerificationError):
    """The bearer token cannot be trusted."""


class MissingScopeError(TokenVerificationError):
    """A valid bearer token lacks the required authorization scope."""


class TokenVerifierUnavailableError(TokenVerificationError):
    """The verifier's signing-key source is temporarily unavailable."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    client_id: str
    scopes: frozenset[str]


class TokenVerifier(Protocol):
    async def verify(self, token: str, *, required_scope: str) -> TokenClaims:
        """Validate a bearer token and return the trusted audit claims."""
