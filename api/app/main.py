"""FastAPI application factory and lifespan.

M0 exposes one endpoint. The shape here — settings injected, resources built
once in the lifespan, routers mounted under the versioned prefix — is what every
later milestone hangs off.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.cache import create_redis
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.routers import health as health_router
from app.services.health import HealthService
from app.storage import create_s3_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the shared clients once, tear them down on shutdown."""
    settings: Settings = app.state.settings

    engine = create_engine(settings)
    redis = create_redis(settings)
    s3 = create_s3_client(settings)

    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.redis = redis
    app.state.s3 = s3
    app.state.health_service = HealthService(
        settings=settings,
        engine=engine,
        redis=redis,
        s3=s3,
    )

    logger.info("api.startup", extra={"environment": settings.environment})
    try:
        yield
    finally:
        await redis.aclose()
        await engine.dispose()
        s3.close()
        logger.info("api.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    logging.basicConfig(level=resolved.log_level.upper())

    app = FastAPI(
        title=resolved.project_name,
        version=resolved.version,
        docs_url="/docs" if resolved.is_local else None,
        redoc_url=None,
        openapi_url="/openapi.json" if resolved.is_local else None,
        lifespan=lifespan,
    )
    app.state.settings = resolved

    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Root mount for container / load-balancer probes. Kept out of the OpenAPI
    # schema so the generated client has exactly one health operation.
    app.include_router(health_router.router, include_in_schema=False)
    # Versioned mount for API clients.
    app.include_router(health_router.router, prefix=resolved.api_v1_prefix)

    return app


app = create_app()
