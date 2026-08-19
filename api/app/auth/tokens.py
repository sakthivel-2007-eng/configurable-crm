"""Token issuing and verification.

Access tokens are stateless JWTs (30 minutes) carrying the user id and nothing
tenant-specific — a token is not scoped to a workspace, the *request path* is,
and membership is re-checked on every request. That means revoking a membership
takes effect immediately rather than at the next token refresh.

Refresh tokens are opaque random strings (30 days). Only their SHA-256 is
stored. Rotation issues a new token in the same family and revokes the old one.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings

__all__ = [
    "AccessTokenClaims",
    "InvalidTokenError",
    "IssuedRefreshToken",
    "TokenService",
]

_ACCESS_TOKEN_TYPE = "access"


class InvalidTokenError(Exception):
    """Raised when a presented token is malformed, expired, or not ours."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """The verified contents of an access token."""

    user_id: uuid.UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    """A freshly minted refresh token.

    `token` is returned to the client exactly once. `token_hash` is what gets
    persisted — a database dump must not yield usable credentials.
    """

    token: str
    token_hash: str
    family_id: uuid.UUID
    expires_at: datetime


class TokenService:
    """Issues and verifies access and refresh tokens."""

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret_key
        self._algorithm = settings.jwt_algorithm
        self._access_ttl = timedelta(minutes=settings.access_token_ttl_minutes)
        self._refresh_ttl = timedelta(days=settings.refresh_token_ttl_days)

    def issue_access_token(self, user_id: uuid.UUID) -> tuple[str, int]:
        """Return the encoded token and its lifetime in seconds."""
        now = datetime.now(UTC)
        expires_at = now + self._access_ttl
        payload = {
            "sub": str(user_id),
            "typ": _ACCESS_TOKEN_TYPE,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": str(uuid.uuid4()),
        }
        encoded = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        return encoded, int(self._access_ttl.total_seconds())

    def verify_access_token(self, token: str) -> AccessTokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                options={"require": ["exp", "sub", "typ"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(str(exc)) from exc

        if payload.get("typ") != _ACCESS_TOKEN_TYPE:
            # A refresh token presented as a bearer token must not authenticate.
            raise InvalidTokenError("wrong token type")

        try:
            user_id = uuid.UUID(payload["sub"])
        except (KeyError, ValueError) as exc:
            raise InvalidTokenError("malformed subject") from exc

        return AccessTokenClaims(
            user_id=user_id,
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )

    def issue_refresh_token(self, family_id: uuid.UUID | None = None) -> IssuedRefreshToken:
        """Mint a refresh token, continuing `family_id` when rotating."""
        raw = secrets.token_urlsafe(48)
        return IssuedRefreshToken(
            token=raw,
            token_hash=self.hash_refresh_token(raw),
            family_id=family_id or uuid.uuid4(),
            expires_at=datetime.now(UTC) + self._refresh_ttl,
        )

    @staticmethod
    def hash_refresh_token(token: str) -> str:
        """SHA-256 of the token.

        Deliberately not argon2: these are 48 bytes of CSPRNG output, not
        user-chosen secrets, so there is nothing to brute-force and the lookup
        needs to be an indexed equality match.
        """
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
