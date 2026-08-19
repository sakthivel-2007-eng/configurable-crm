"""The workspace-scoping dependency.

Resolves `workspace_id` from the path, verifies the caller holds an active,
licensed membership in it, loads their permission template, computes the set of
memberships whose records they may see, and hands back a `ScopedSession`.

The visibility set is the important part. "A manager sees their reports' leads"
is a *data-access* rule, so it is resolved once, here, into
`visible_membership_ids`, and every downstream query filters on it. No endpoint
reimplements the hierarchy walk, and no endpoint can forget to.

A caller who is not a member of the requested workspace gets 404, not 403 — a
403 would confirm the workspace exists.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import CurrentUser, get_current_user
from app.dependencies import get_session
from app.errors import forbidden, not_found
from app.models import Membership, PermissionTemplate, Workspace
from app.tenancy.session import ScopedSession

__all__ = [
    "WorkspaceScope",
    "require_workspace",
    "require_workspace_admin",
    "resolve_visible_membership_ids",
]


@dataclass(frozen=True, slots=True)
class WorkspaceScope:
    """Everything a handler needs to know about who is asking and where.

    Handlers receive this instead of a raw workspace id, so there is no way to
    hold a workspace id without also holding the membership that justifies it.
    """

    workspace: Workspace
    membership: Membership
    template: PermissionTemplate
    session: ScopedSession
    # Memberships whose owned records this caller may read: themselves, plus
    # every report transitively beneath them, or every member of the workspace
    # if their template grants admin access.
    visible_membership_ids: frozenset[uuid.UUID]
    sees_all_members: bool

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.workspace.id

    @property
    def membership_id(self) -> uuid.UUID:
        return self.membership.id

    def capability(self, group: str, name: str) -> bool:
        """Read one capability flag out of the template.

        Absent means denied. M4 replaces the raw dict access with a validated
        capability model; the call sites do not change.
        """
        group_caps = self.template.capabilities.get(group)
        if not isinstance(group_caps, dict):
            return False
        return bool(group_caps.get(name, False))

    @property
    def is_workspace_admin(self) -> bool:
        return self.capability("leads", "admin_access") or self.capability(
            "permissions", "admin_access"
        )


async def resolve_visible_membership_ids(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> frozenset[uuid.UUID]:
    """The caller's membership plus every report transitively beneath it.

    One recursive CTE rather than N round-trips, and bounded by `workspace_id`
    at every level so a mis-set `manager_id` cannot walk across tenants.
    """
    base = (
        select(Membership.id.label("id"))
        .where(
            Membership.id == membership_id,
            Membership.workspace_id == workspace_id,
        )
        .cte("visible_memberships", recursive=True)
    )
    reports = select(Membership.id).where(
        Membership.manager_id == base.c.id,
        Membership.workspace_id == workspace_id,
    )
    hierarchy = base.union_all(reports)

    result = await session.execute(select(hierarchy.c.id))
    return frozenset(result.scalars().all())


async def require_workspace(
    workspace_id: Annotated[uuid.UUID, Path(description="Workspace the request targets")],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AsyncIterator[WorkspaceScope]:
    """Resolve and authorise the workspace for this request.

    Every tenant endpoint depends on this. A handler that queries tenant data
    without it is a bug — and, because tenant repositories require a
    `ScopedSession` that only this dependency produces, a bug that does not
    type-check.
    """
    membership_row = await session.execute(
        select(Membership)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == current_user.id,
        )
        .options(
            selectinload(Membership.template),
            selectinload(Membership.workspace),
        )
        .limit(1)
    )
    membership = membership_row.scalar_one_or_none()

    # Not a member, or the workspace does not exist: identical response. A 403
    # here would confirm the workspace id is real.
    if membership is None:
        raise not_found("Workspace")

    if not membership.is_active:
        raise forbidden("member_inactive", "This membership has been deactivated")
    if not membership.has_license:
        raise forbidden("no_license", "This membership does not hold a licence")
    if not membership.workspace.is_active:
        raise not_found("Workspace")

    scope_ids = await resolve_visible_membership_ids(
        session,
        workspace_id=workspace_id,
        membership_id=membership.id,
    )

    scoped = ScopedSession(session, workspace_id)
    template = membership.template
    sees_all = _grants_admin_access(template.capabilities)

    if sees_all:
        all_ids = await session.execute(
            select(Membership.id).where(Membership.workspace_id == workspace_id)
        )
        scope_ids = frozenset(all_ids.scalars().all())

    yield WorkspaceScope(
        workspace=membership.workspace,
        membership=membership,
        template=template,
        session=scoped,
        visible_membership_ids=scope_ids,
        sees_all_members=sees_all,
    )


async def require_workspace_admin(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> WorkspaceScope:
    """Same as `require_workspace`, but the caller must hold admin access.

    Used by the settings and member-administration endpoints.
    """
    if not scope.is_workspace_admin:
        raise forbidden(
            "insufficient_permissions",
            "This action requires workspace administrator access",
        )
    return scope


def _grants_admin_access(capabilities: dict[str, Any]) -> bool:
    for group in ("leads", "permissions"):
        group_caps = capabilities.get(group)
        if isinstance(group_caps, dict) and group_caps.get("admin_access"):
            return True
    return False
