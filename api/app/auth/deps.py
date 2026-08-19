"""Dependencies that answer "who is calling?".

Deliberately *not* "what may they touch?" — that is `app.tenancy.scoping`. The
split matters: an access token identifies a user across every workspace they
belong to, and membership is re-checked per request, so revoking someone's
membership takes effect on their next call rather than at their next refresh.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import PasswordHasherService
from app.auth.rate_limit import AuthRateLimiter
from app.auth.tokens import InvalidTokenError, TokenService
from app.config import Settings
from app.dependencies import get_session
from app.errors import unauthorized
from app.models import User

__all__ = [
    "CurrentUser",
    "get_auth_rate_limiter",
    "get_current_user",
    "get_password_hasher",
    "get_token_service",
]

CurrentUser = User

# auto_error=False so a missing header raises our error shape rather than
# FastAPI's bare {"detail": "Not authenticated"}.
_bearer = HTTPBearer(auto_error=False)


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("Settings are not initialised; check the app factory")
    return settings


def get_token_service(request: Request) -> TokenService:
    return TokenService(_settings(request))


def get_password_hasher(request: Request) -> PasswordHasherService:
    # Built once in the lifespan: argon2 parameter setup is cheap, but the
    # dummy hash computed in the constructor is not.
    hasher = getattr(request.app.state, "password_hasher", None)
    if not isinstance(hasher, PasswordHasherService):
        raise RuntimeError("PasswordHasherService is not initialised; check the app lifespan")
    return hasher


def get_auth_rate_limiter(request: Request) -> AuthRateLimiter:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise RuntimeError("Redis is not initialised; check the app lifespan")
    return AuthRateLimiter(redis, _settings(request))


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve the bearer token to a live user.

    The user row is loaded on every request rather than trusted from the token,
    so deactivating an account logs it out immediately instead of leaving a
    valid token working for up to 30 minutes.
    """
    if credentials is None or not credentials.credentials:
        raise unauthorized("not_authenticated", "Authentication credentials were not provided")

    try:
        claims = tokens.verify_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise unauthorized("invalid_token", "The access token is invalid or has expired") from exc

    result = await session.execute(select(User).where(User.id == claims.user_id).limit(1))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise unauthorized("invalid_token", "The access token is invalid or has expired")

    return user
