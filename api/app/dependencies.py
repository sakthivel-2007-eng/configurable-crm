"""Shared FastAPI dependencies.

Resources built once in the application lifespan are handed to handlers from
here, which also gives tests a single override point.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import session_scope
from app.services.health import HealthService


def get_health_service(request: Request) -> HealthService:
    service = getattr(request.app.state, "health_service", None)
    if not isinstance(service, HealthService):
        raise RuntimeError("HealthService is not initialised; check the app lifespan")
    return service


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise RuntimeError("Session factory is not initialised; check the app lifespan")
    return factory  # type: ignore[no-any-return]  # app.state is untyped by design


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """An unscoped session.

    M1 wraps this in the workspace-scoping dependency; tenant data must never be
    queried through this directly.
    """
    async for session in session_scope(get_session_factory(request)):
        yield session
