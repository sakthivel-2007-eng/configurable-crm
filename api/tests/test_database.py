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


async def test_server_is_postgres_16_or_newer(engine: AsyncEngine) -> None:
    """The data model targets Postgres 16.

    CI pins 16 through testcontainers. A developer pointing `TEST_DATABASE_URL`
    at a newer local server is fine — what must not pass silently is an older
    one, which would lack features M2 onward depends on.
    """
    async with engine.connect() as connection:
        version = await connection.scalar(text("SHOW server_version_num"))

    assert int(str(version)) >= 160000, f"Postgres 16+ required, found {version}"


async def test_session_factory_yields_a_working_session(engine: AsyncEngine) -> None:
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        value = await session.scalar(text("SELECT 42"))

    assert value == 42


def test_m1_defines_exactly_its_own_tables() -> None:
    """M1 owns six tables and no more.

    A table appearing here early is the "building ahead" failure mode CLAUDE.md
    warns about — and if it is a taxonomy table, the worse one. Each milestone
    extends this set deliberately.
    """
    assert set(Base.metadata.tables) == {
        "users",
        "refresh_tokens",
        "workspaces",
        "permission_templates",
        "memberships",
        "availability_log",
    }


def test_every_tenant_table_carries_workspace_id() -> None:
    """Architecture rule 1, asserted structurally rather than by review.

    `users`, `workspaces` and `refresh_tokens` are the only legitimate
    exceptions: a user is global, a workspace *is* the tenant, and a refresh
    token belongs to a user across every workspace they hold.
    """
    global_tables = {"users", "workspaces", "refresh_tokens"}

    for name, table in Base.metadata.tables.items():
        if name in global_tables:
            continue
        assert "workspace_id" in table.columns, f"{name} holds tenant data without workspace_id"
