"""HealthService probes, exercised against real infrastructure and real failures."""

from __future__ import annotations

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.services.health import HealthService
from app.storage import create_s3_client

pytestmark = pytest.mark.integration

# Ports chosen to be closed, so the probes fail the way a dead dependency would.
UNREACHABLE_REDIS_URL = "redis://127.0.0.1:6399/0"
UNREACHABLE_S3_ENDPOINT = "http://127.0.0.1:9099"


def _unreachable_dependencies(settings: Settings) -> tuple[Redis, object]:
    redis = Redis.from_url(UNREACHABLE_REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    s3_settings = settings.model_copy(update={"s3_endpoint_url": UNREACHABLE_S3_ENDPOINT})
    return redis, create_s3_client(s3_settings)


async def test_database_probe_passes_and_dead_dependencies_degrade_the_report(
    settings: Settings, engine: AsyncEngine
) -> None:
    redis, s3 = _unreachable_dependencies(settings)
    service = HealthService(settings=settings, engine=engine, redis=redis, s3=s3)  # type: ignore[arg-type]

    try:
        report = await service.check()
    finally:
        await redis.aclose()

    assert report.checks.database.status == "ok"
    assert report.checks.database.latency_ms is not None
    assert report.checks.redis.status == "error"
    assert report.checks.object_storage.status == "error"
    assert report.status == "degraded"


async def test_probe_errors_carry_detail_in_local_environments(
    settings: Settings, engine: AsyncEngine
) -> None:
    redis, s3 = _unreachable_dependencies(settings)
    service = HealthService(settings=settings, engine=engine, redis=redis, s3=s3)  # type: ignore[arg-type]

    try:
        report = await service.check()
    finally:
        await redis.aclose()

    assert settings.is_local
    assert report.checks.redis.error is not None
    assert ":" in report.checks.redis.error


async def test_probe_errors_are_redacted_outside_local(
    settings: Settings, engine: AsyncEngine
) -> None:
    """Driver errors can echo a DSN, credentials included."""
    production = settings.model_copy(update={"environment": "production"})
    redis, s3 = _unreachable_dependencies(settings)
    service = HealthService(settings=production, engine=engine, redis=redis, s3=s3)  # type: ignore[arg-type]

    try:
        report = await service.check()
    finally:
        await redis.aclose()

    assert report.checks.redis.error is not None
    assert ":" not in report.checks.redis.error
