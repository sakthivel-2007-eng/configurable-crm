"""Fixtures for the cross-workspace isolation suite.

Two workspaces, A and B, provisioned identically and populated with the same
shapes of data. Every test in this package asks the same question in a different
way: can a member of A observe *anything* about B?

The answer must always be 404 — never 403. A 403 confirms the resource exists,
which is itself a leak: it tells an attacker which ids are real, and over enough
probes, how big a competitor's pipeline is.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import WorkspaceFixture, add_member, build_workspace, login

from app.auth.passwords import PasswordHasherService


@dataclass(slots=True)
class TenantPair:
    """Two workspaces that must never see each other."""

    a: WorkspaceFixture
    b: WorkspaceFixture


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


@pytest.fixture
async def tenants(
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    api: AsyncClient,
) -> TenantPair:
    """Workspace A and workspace B, each with an owner, a manager and a rep.

    The manager/rep pair exists so the hierarchy-visibility rule is exercised
    from the same fixture — a manager in A must not see a rep in B even though
    the shapes match exactly.
    """
    workspace_a = await build_workspace(
        db_session,
        hasher,
        name="Tenant A",
        owner_email="owner-a@example.com",
    )
    workspace_b = await build_workspace(
        db_session,
        hasher,
        name="Tenant B",
        owner_email="owner-b@example.com",
    )

    for fixture, suffix in ((workspace_a, "a"), (workspace_b, "b")):
        manager = await add_member(
            db_session,
            hasher,
            fixture,
            key="manager",
            email=f"manager-{suffix}@example.com",
            template_name="Manager",
        )
        await add_member(
            db_session,
            hasher,
            fixture,
            key="rep",
            email=f"rep-{suffix}@example.com",
            template_name="Caller",
            manager=manager,
        )

    for fixture in (workspace_a, workspace_b):
        await login(api, fixture.owner)
        for actor in fixture.members.values():
            await login(api, actor)

    return TenantPair(a=workspace_a, b=workspace_b)
