"""The scoping layer itself.

The HTTP-level isolation suite proves the *outcome*. These tests prove the
*mechanism* — that `ScopedSession` is what enforces it, so a future endpoint
that bypasses a service and queries directly is still contained.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import WorkspaceFixture, add_member, build_workspace

from app.auth.passwords import PasswordHasherService
from app.models import Membership, PermissionTemplate
from app.tenancy.scoping import resolve_visible_membership_ids
from app.tenancy.session import ScopedSession, WorkspaceMismatchError

pytestmark = pytest.mark.integration


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


@pytest.fixture
async def two_workspaces(
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> tuple[WorkspaceFixture, WorkspaceFixture]:
    first = await build_workspace(db_session, hasher, name="One", owner_email="one@example.com")
    second = await build_workspace(db_session, hasher, name="Two", owner_email="two@example.com")
    return first, second


async def test_scoped_get_returns_none_for_another_workspaces_row(
    db_session: AsyncSession,
    two_workspaces: tuple[WorkspaceFixture, WorkspaceFixture],
) -> None:
    first, second = two_workspaces
    scoped = ScopedSession(db_session, first.id)

    assert await scoped.get(Membership, first.owner.membership.id) is not None
    assert await scoped.get(Membership, second.owner.membership.id) is None


async def test_scoped_select_excludes_other_workspaces(
    db_session: AsyncSession,
    two_workspaces: tuple[WorkspaceFixture, WorkspaceFixture],
) -> None:
    first, _second = two_workspaces
    scoped = ScopedSession(db_session, first.id)

    rows = await scoped.execute(scoped.select(PermissionTemplate))
    workspace_ids = {template.workspace_id for template in rows.scalars().all()}
    assert workspace_ids == {first.id}


async def test_the_orm_criteria_filters_even_an_unfiltered_statement(
    db_session: AsyncSession,
    two_workspaces: tuple[WorkspaceFixture, WorkspaceFixture],
) -> None:
    """The third enforcement layer, tested on its own.

    This statement carries no `workspace_id` predicate at all — exactly the
    query a future contributor writes when they forget. The `do_orm_execute`
    listener adds the criteria anyway.
    """
    first, second = two_workspaces
    scoped = ScopedSession(db_session, first.id)

    unfiltered = select(Membership)  # deliberately missing the scope
    rows = await scoped.execute(unfiltered)
    workspace_ids = {membership.workspace_id for membership in rows.scalars().all()}

    assert workspace_ids == {first.id}
    assert second.id not in workspace_ids


async def test_adding_a_row_stamps_the_workspace(
    db_session: AsyncSession,
    two_workspaces: tuple[WorkspaceFixture, WorkspaceFixture],
) -> None:
    first, _ = two_workspaces
    scoped = ScopedSession(db_session, first.id)

    template = PermissionTemplate(name="Bespoke", capabilities={})
    scoped.add(template)
    await scoped.flush()

    assert template.workspace_id == first.id


async def test_adding_a_row_belonging_to_another_workspace_raises(
    db_session: AsyncSession,
    two_workspaces: tuple[WorkspaceFixture, WorkspaceFixture],
) -> None:
    """Silently re-stamping it would write one tenant's data under another's id."""
    first, second = two_workspaces
    scoped = ScopedSession(db_session, first.id)

    foreign = PermissionTemplate(workspace_id=second.id, name="Smuggled", capabilities={})

    with pytest.raises(WorkspaceMismatchError):
        scoped.add(foreign)


async def test_add_global_refuses_tenant_data(
    db_session: AsyncSession,
    two_workspaces: tuple[WorkspaceFixture, WorkspaceFixture],
) -> None:
    """`add_global` exists for `User`. It must not become a way around scoping."""
    first, _ = two_workspaces
    scoped = ScopedSession(db_session, first.id)

    with pytest.raises(WorkspaceMismatchError, match="tenant data"):
        scoped.add_global(PermissionTemplate(name="Sneaky", capabilities={}))


async def test_visibility_resolves_the_whole_report_chain(
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    two_workspaces: tuple[WorkspaceFixture, WorkspaceFixture],
) -> None:
    """Three levels deep: a director sees their manager and that manager's rep."""
    first, _ = two_workspaces

    director = await add_member(
        db_session, hasher, first, key="director", email="dir@example.com", template_name="Manager"
    )
    manager = await add_member(
        db_session,
        hasher,
        first,
        key="manager",
        email="mgr@example.com",
        template_name="Manager",
        manager=director,
    )
    rep = await add_member(
        db_session,
        hasher,
        first,
        key="rep",
        email="rep@example.com",
        template_name="Caller",
        manager=manager,
    )

    visible = await resolve_visible_membership_ids(
        db_session,
        workspace_id=first.id,
        membership_id=director.membership.id,
    )

    assert visible == {
        director.membership.id,
        manager.membership.id,
        rep.membership.id,
    }
    assert first.owner.membership.id not in visible


async def test_visibility_of_a_leaf_member_is_just_themselves(
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    two_workspaces: tuple[WorkspaceFixture, WorkspaceFixture],
) -> None:
    first, _ = two_workspaces
    rep = await add_member(
        db_session, hasher, first, key="rep", email="leaf@example.com", template_name="Caller"
    )

    visible = await resolve_visible_membership_ids(
        db_session,
        workspace_id=first.id,
        membership_id=rep.membership.id,
    )
    assert visible == {rep.membership.id}


async def test_visibility_never_crosses_a_workspace_boundary(
    db_session: AsyncSession,
    two_workspaces: tuple[WorkspaceFixture, WorkspaceFixture],
) -> None:
    """Asking for a membership id that lives in the other workspace yields
    nothing, rather than that membership's subtree."""
    first, second = two_workspaces

    visible = await resolve_visible_membership_ids(
        db_session,
        workspace_id=first.id,
        membership_id=second.owner.membership.id,
    )
    assert visible == frozenset()


async def test_visibility_of_an_unknown_membership_is_empty(
    db_session: AsyncSession,
    two_workspaces: tuple[WorkspaceFixture, WorkspaceFixture],
) -> None:
    first, _ = two_workspaces
    visible = await resolve_visible_membership_ids(
        db_session,
        workspace_id=first.id,
        membership_id=uuid.uuid4(),
    )
    assert visible == frozenset()
