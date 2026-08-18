"""Shared test fixtures.

Environment defaults are set before `app` is imported, because Settings has no
hardcoded fallbacks — every value must come from the environment.

Postgres comes from a real container: JSONB and expression indexes, which this
product leans on from M2 onward, do not behave the same on SQLite.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://crm:crm@localhost:5432/crm")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("S3_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("S3_SECRET_ACCESS_KEY", "test-secret-key")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("CORS_ORIGINS", '["http://localhost:5173"]')

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from app.config import Settings, get_settings
from app.main import create_app

POSTGRES_IMAGE = "postgres:16-alpine"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """A throwaway Postgres 16, shared by the whole test session."""
    with PostgresContainer(POSTGRES_IMAGE) as container:
        yield container


@pytest.fixture(scope="session")
def postgres_async_dsn(postgres_container: PostgresContainer) -> str:
    """asyncpg DSN for the container.

    Built by hand rather than via `get_connection_url`, whose readiness probe
    uses a synchronous driver.
    """
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    return (
        f"postgresql+asyncpg://{postgres_container.username}:"
        f"{postgres_container.password}@{host}:{port}/{postgres_container.dbname}"
    )


@pytest.fixture
async def engine(postgres_async_dsn: str) -> AsyncIterator[AsyncEngine]:
    created = create_async_engine(postgres_async_dsn, poolclass=None)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """The application without its lifespan.

    ASGITransport does not run lifespan events, so no real client is built —
    tests inject exactly the dependencies they need.
    """
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
