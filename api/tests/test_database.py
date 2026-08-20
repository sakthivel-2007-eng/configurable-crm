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


#: Tables each milestone is allowed to define. A table appearing before its
#: milestone is the "building ahead" failure mode CLAUDE.md warns about — and
#: if it is a taxonomy table, the worse one. Extend this deliberately, one
#: milestone at a time; never to make a red test green.
M1_TABLES = {
    "users",
    "refresh_tokens",
    "workspaces",
    "permission_templates",
    "memberships",
    "availability_log",
}

M2_TABLES = {
    "lead_fields",
    "field_options",
    # `custom_action_types` lands with M2 because `action_fields` holds a
    # foreign key to it; M3 adds its behaviour, not its table.
    "custom_action_types",
    "action_fields",
    "action_field_options",
    "indexed_fields",
}


M3_TABLES = {
    "stages",
    "lost_reasons",
    "call_dispositions",
}

M4_TABLES = {
    "template_field_grants",
    "template_lead_views",
}

M5_TABLES = {
    "leads",
    "actions",
    "action_attachments",
    "changesets",
    "message_templates",
}


def test_the_schema_defines_exactly_the_tables_the_landed_milestones_own() -> None:
    """No table exists before the milestone that owns it."""
    assert set(Base.metadata.tables) == (M1_TABLES | M2_TABLES | M3_TABLES | M4_TABLES | M5_TABLES)


def test_no_table_name_encodes_a_business_concept() -> None:
    """Tables are product concepts. A customer's taxonomy lives in rows.

    A `courses` or `applications` table would mean the schema had learned
    something about one customer's business.
    """
    forbidden = {"course", "student", "application", "product", "enquiry", "admission"}
    for name in Base.metadata.tables:
        assert not (forbidden & set(name.split("_"))), f"{name} names a business concept"


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
