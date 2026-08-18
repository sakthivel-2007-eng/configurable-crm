"""Integration tests against a real Postgres 16 container."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import Base, create_session_factory

pytestmark = pytest.mark.integration


async def test_engine_connects_to_postgres(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))

    assert result.scalar_one() == 1


async def test_server_is_postgres_16(engine: AsyncEngine) -> None:
    """The data model targets Postgres 16 features; assert we are on it."""
    async with engine.connect() as connection:
        version = await connection.scalar(text("SHOW server_version"))

    assert str(version).startswith("16.")


async def test_session_factory_yields_a_working_session(engine: AsyncEngine) -> None:
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        value = await session.scalar(text("SELECT 42"))

    assert value == 42


def test_metadata_is_empty_in_m0() -> None:
    """M0 defines no tables. The first model arrives with M1's tenancy tables."""
    assert Base.metadata.tables == {}
