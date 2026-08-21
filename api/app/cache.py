"""Redis client construction.

Redis backs background work (`arq`) and rate limiting from later milestones. M0
only needs a connection to report on.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.config import Settings


def create_redis(settings: Settings) -> Redis:
    """Build the shared Redis client. One per process, closed in the lifespan."""
    # Annotated rather than returned directly: `from_url` is untyped in
    # redis>=5.3, so returning it straight through trips mypy's no-any-return.
    client: Redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.health_check_timeout_seconds,
        socket_timeout=settings.health_check_timeout_seconds,
        health_check_interval=30,
    )
    return client
