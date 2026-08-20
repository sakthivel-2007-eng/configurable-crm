"""Fixture builders.

`build_workspace` provisions through the real `WorkspaceProvisioner`, so the
tests exercise the same code path a customer signup does. Nothing here invents
a business taxonomy — the whole point of the isolation suite is that two
workspaces are structurally identical and still cannot see each other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.passwords import PasswordHasherService
from app.models import LeadField, Membership, PermissionTemplate, User, Workspace
from app.services.provisioning import WorkspaceProvisioner

__all__ = ["Actor", "WorkspaceFixture", "build_workspace", "login"]

# Every fixture user shares this password. It is not a secret — it exists so the
# login endpoint has something to verify.
TEST_PASSWORD = "correct-horse-battery-staple"


@dataclass(slots=True)
class Actor:
    """A user plus their membership and a bearer token."""

    user: User
    membership: Membership
    access_token: str = ""
    refresh_token: str = ""

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@dataclass(slots=True)
class WorkspaceFixture:
    """One tenant with an owner and whatever extra members a test asked for."""

    workspace: Workspace
    owner: Actor
    templates: dict[str, PermissionTemplate]
    members: dict[str, Actor] = field(default_factory=dict)
    #: The four built-in lead fields M2 provisions, by key. Gives the isolation
    #: suite a real, always-present tenant resource to aim a probe at.
    fields: dict[str, LeadField] = field(default_factory=dict)

    @property
    def id(self) -> uuid.UUID:
        return self.workspace.id

    @property
    def builtin_field_id(self) -> uuid.UUID:
        """A field that exists in every workspace, for cross-tenant probes."""
        return self.fields["phone"].id

    def path(self, suffix: str = "") -> str:
        return f"/api/v1/workspaces/{self.workspace.id}{suffix}"


async def build_workspace(
    session: AsyncSession,
    hasher: PasswordHasherService,
    *,
    name: str,
    owner_email: str,
) -> WorkspaceFixture:
    """Provision a workspace through the production provisioner."""
    owner_user = User(
        email=owner_email,
        full_name=f"{name} Owner",
        password_hash=hasher.hash(TEST_PASSWORD),
    )
    session.add(owner_user)
    await session.flush()

    provisioner = WorkspaceProvisioner(session)
    workspace, membership = await provisioner.provision(name=name, owner=owner_user)
    await session.commit()

    templates = await _templates(session, workspace.id)
    membership = await _reload_membership(session, membership.id)
    fields = await _lead_fields(session, workspace.id)

    return WorkspaceFixture(
        workspace=workspace,
        owner=Actor(user=owner_user, membership=membership),
        templates=templates,
        fields=fields,
    )


async def _lead_fields(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, LeadField]:
    """The workspace's lead fields, keyed by their derived JSONB key."""
    rows = await session.execute(select(LeadField).where(LeadField.workspace_id == workspace_id))
    return {f.key: f for f in rows.scalars().all()}


async def add_member(
    session: AsyncSession,
    hasher: PasswordHasherService,
    fixture: WorkspaceFixture,
    *,
    key: str,
    email: str,
    template_name: str,
    manager: Actor | None = None,
    has_license: bool = True,
) -> Actor:
    """Add a member to an existing workspace fixture."""
    user = User(
        email=email,
        full_name=f"{key.title()} {fixture.workspace.name}",
        password_hash=hasher.hash(TEST_PASSWORD),
    )
    session.add(user)
    await session.flush()

    membership = Membership(
        workspace_id=fixture.workspace.id,
        user_id=user.id,
        template_id=fixture.templates[template_name].id,
        manager_id=manager.membership.id if manager else None,
        has_license=has_license,
    )
    session.add(membership)
    await session.commit()

    actor = Actor(user=user, membership=await _reload_membership(session, membership.id))
    fixture.members[key] = actor
    return actor


async def login(api: AsyncClient, actor: Actor) -> Actor:
    """Authenticate an actor and stash their tokens on it."""
    response = await api.post(
        "/api/v1/auth/login",
        json={"email": actor.user.email, "password": TEST_PASSWORD},
    )
    response.raise_for_status()
    body = response.json()
    actor.access_token = body["access_token"]
    actor.refresh_token = body["refresh_token"]
    return actor


async def _templates(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> dict[str, PermissionTemplate]:
    rows = await session.execute(
        select(PermissionTemplate).where(PermissionTemplate.workspace_id == workspace_id)
    )
    return {template.name: template for template in rows.scalars().all()}


async def _reload_membership(session: AsyncSession, membership_id: uuid.UUID) -> Membership:
    rows = await session.execute(
        select(Membership)
        .where(Membership.id == membership_id)
        .options(
            selectinload(Membership.user),
            selectinload(Membership.template),
            selectinload(Membership.workspace),
        )
    )
    return rows.scalar_one()
