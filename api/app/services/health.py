"""Health probing for the backing services.

Each probe is bounded by a timeout and returns a result rather than raising, so
one dead dependency degrades the report instead of failing the request.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.schemas.health import ComponentHealth, HealthChecks, HealthResponse

if TYPE_CHECKING:  # boto3-stubs is a dev dependency, absent at runtime
    from mypy_boto3_s3.client import S3Client

_MAX_ERROR_CHARS = 200

type Probe = Callable[[], Awaitable[None]]


class HealthService:
    """Probes Postgres, Redis and object storage concurrently."""

    def __init__(
        self,
        *,
        settings: Settings,
        engine: AsyncEngine,
        redis: Redis,
        s3: S3Client,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._redis = redis
        self._s3 = s3

    async def check(self) -> HealthResponse:
        database, redis, object_storage = await asyncio.gather(
            self._check_database(),
            self._check_redis(),
            self._check_object_storage(),
        )
        checks = HealthChecks(
            database=database,
            redis=redis,
            object_storage=object_storage,
        )
        degraded = any(component.status != "ok" for component in (database, redis, object_storage))
        return HealthResponse(
            status="degraded" if degraded else "ok",
            service=self._settings.project_name,
            version=self._settings.version,
            environment=self._settings.environment,
            checks=checks,
        )

    # --- individual probes -------------------------------------------------

    async def _check_database(self) -> ComponentHealth:
        async def probe() -> None:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

        return await self._timed(probe)

    async def _check_redis(self) -> ComponentHealth:
        async def probe() -> None:
            await self._redis.ping()

        return await self._timed(probe)

    async def _check_object_storage(self) -> ComponentHealth:
        bucket = self._settings.s3_bucket

        async def probe() -> None:
            # boto3 is synchronous; keep the event loop free.
            await asyncio.to_thread(self._s3.head_bucket, Bucket=bucket)

        return await self._timed(probe)

    # --- helpers -----------------------------------------------------------

    async def _timed(self, probe: Probe) -> ComponentHealth:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._settings.health_check_timeout_seconds):
                await probe()
        except Exception as exc:  # a probe failure is a report, not a 500
            return ComponentHealth(
                status="error",
                latency_ms=self._elapsed_ms(started),
                error=self._describe(exc),
            )
        return ComponentHealth(status="ok", latency_ms=self._elapsed_ms(started))

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 2)

    def _describe(self, exc: BaseException) -> str:
        """Exception detail, redacted outside local/test.

        Driver errors can echo the DSN, credentials included, so deployed
        environments get the exception type only.
        """
        name = type(exc).__name__
        if not self._settings.is_local:
            return name
        message = str(exc).strip()
        if not message:
            return name
        if len(message) > _MAX_ERROR_CHARS:
            message = message[:_MAX_ERROR_CHARS] + "…"
        return f"{name}: {message}"
