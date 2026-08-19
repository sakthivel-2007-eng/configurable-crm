"""New-workspace provisioning.

`POST /workspaces` creates **structure, never taxonomy** (docs/01-data-model.md
§7). A new customer sees an empty, working CRM and builds their own vocabulary.
Nothing here may name a product, an industry stage, or a business status.

## Why this is a registry

§7 lists five things to provision: 4 built-in lead fields, 4 stages, 5 lost
reasons, 7 system call dispositions, 5 permission templates. Only the last of
those has a table in M1 — `lead_fields`, `stages`, `lost_reasons` and
`call_dispositions` arrive with M2 and M3.

Rather than pull three milestones of schema forward, each milestone registers
its own provisioning step here. `EXPECTED_STEPS` names the complete §7 set, and
`test_provisioning.py` asserts that every registered step is one of them and
reports which remain outstanding — so the list cannot quietly drift from the
spec, and M2/M3 plug in without editing this file's callers.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Membership, PermissionTemplate, User, Workspace

__all__ = [
    "EXPECTED_STEPS",
    "ProvisioningRegistry",
    "ProvisioningStep",
    "WorkspaceProvisioner",
    "provisioning_registry",
    "slugify",
]

# The five default permission templates (03-configuration-model.md §6.1).
# These are *product* roles — the shape of a sales team, not one customer's
# vocabulary. A workspace admin renames, edits or deletes any of them except
# Root, and creates as many more as they like.
ROOT_TEMPLATE_NAME = "Root"
_ADMIN = "Admin"
_MANAGER = "Manager"
_CALLER = "Caller"
_MARKETING = "Marketing"

# Capability defaults. M4 replaces this dict with a validated capability model
# covering all 10 Access groups and 3 View groups; M1 populates only what the
# scoping layer reads today, so nothing here pretends to be complete.
_ROOT_CAPS: dict[str, Any] = {
    "leads": {
        "admin_access": True,
        "manually_add_lead": True,
        "bulk_edit": True,
        "actions": True,
        "merge_leads": True,
        "search": True,
    },
    "permissions": {"admin_access": True},
    "team": {"admin_access": True},
}
_ADMIN_CAPS: dict[str, Any] = {
    "leads": {
        "admin_access": True,
        "manually_add_lead": True,
        "bulk_edit": True,
        "actions": True,
        "merge_leads": True,
        "search": True,
    },
    "permissions": {"admin_access": True},
    "team": {"admin_access": True},
}
_MANAGER_CAPS: dict[str, Any] = {
    "leads": {
        "admin_access": False,
        "manually_add_lead": True,
        "bulk_edit": True,
        "actions": True,
        "merge_leads": True,
        "search": True,
    },
    "team": {"admin_access": False},
}
_CALLER_CAPS: dict[str, Any] = {
    "leads": {
        "admin_access": False,
        "manually_add_lead": True,
        "bulk_edit": False,
        "actions": True,
        "merge_leads": False,
        "search": True,
    },
}
_MARKETING_CAPS: dict[str, Any] = {
    "leads": {
        "admin_access": False,
        "manually_add_lead": True,
        "bulk_edit": False,
        "actions": False,
        "merge_leads": False,
        "search": True,
    },
}

_DEFAULT_TEMPLATES: tuple[tuple[str, bool, dict[str, Any]], ...] = (
    # (name, is_readonly, capabilities)
    (ROOT_TEMPLATE_NAME, True, _ROOT_CAPS),
    (_ADMIN, False, _ADMIN_CAPS),
    (_MANAGER, False, _MANAGER_CAPS),
    (_CALLER, False, _CALLER_CAPS),
    (_MARKETING, False, _MARKETING_CAPS),
)

# The complete §7 provisioning set. Steps are registered by the milestone that
# owns the tables they write; this constant is the contract they are checked
# against.
EXPECTED_STEPS: frozenset[str] = frozenset(
    {
        "permission_templates",  # M1 — this module
        "lead_fields",  # M2 — 4 built-ins, plus identity and H1/H2 pointers
        "stages",  # M3 — 1 initial, 1 active, 1 won, 1 lost
        "lost_reasons",  # M3 — 5 defaults, one of them the fallback
        "call_dispositions",  # M3 — the 7 system entries, one default
    }
)


@dataclass(frozen=True, slots=True)
class ProvisioningStep:
    """One milestone's contribution to a new workspace."""

    name: str
    run: Callable[[AsyncSession, Workspace], Awaitable[None]]


class ProvisioningRegistry:
    """Ordered collection of provisioning steps.

    Order matters: M2's step sets `identity_field_id` on the workspace, which
    M3's stages do not depend on but M4's field grants will.
    """

    def __init__(self) -> None:
        self._steps: list[ProvisioningStep] = []

    def register(self, step: ProvisioningStep) -> None:
        if step.name not in EXPECTED_STEPS:
            raise ValueError(
                f"Unknown provisioning step {step.name!r}. Provisioning creates only the "
                f"structure listed in docs/01-data-model.md §7; add the step there first."
            )
        if any(existing.name == step.name for existing in self._steps):
            raise ValueError(f"Provisioning step {step.name!r} is already registered")
        self._steps.append(step)

    @property
    def registered_names(self) -> frozenset[str]:
        return frozenset(step.name for step in self._steps)

    @property
    def outstanding_names(self) -> frozenset[str]:
        """§7 entries whose milestone has not landed yet."""
        return EXPECTED_STEPS - self.registered_names

    async def run_all(self, session: AsyncSession, workspace: Workspace) -> None:
        for step in self._steps:
            await step.run(session, workspace)


provisioning_registry = ProvisioningRegistry()


async def _provision_permission_templates(session: AsyncSession, workspace: Workspace) -> None:
    """Create the five default templates.

    Root is `is_readonly`: it is the escape hatch that must keep working when
    an admin misconfigures everything else, so it cannot be edited into
    uselessness.
    """
    session.add_all(
        [
            PermissionTemplate(
                workspace_id=workspace.id,
                name=name,
                is_system=True,
                is_readonly=is_readonly,
                capabilities=capabilities,
            )
            for name, is_readonly, capabilities in _DEFAULT_TEMPLATES
        ]
    )
    await session.flush()


provisioning_registry.register(
    ProvisioningStep(name="permission_templates", run=_provision_permission_templates)
)


class WorkspaceProvisioner:
    """Creates a workspace and everything §7 says comes with it."""

    def __init__(self, session: AsyncSession, registry: ProvisioningRegistry | None = None) -> None:
        self._session = session
        self._registry = registry or provisioning_registry

    async def provision(
        self,
        *,
        name: str,
        owner: User,
        slug: str | None = None,
        default_country_code: str = "91",
        timezone: str = "Asia/Kolkata",
        currency: str = "INR",
        seat_limit: int = 3,
    ) -> tuple[Workspace, Membership]:
        """Create the workspace and make `owner` its first licensed member.

        The owner gets Root: a workspace whose only administrator can be locked
        out by an editable template is a support ticket waiting to happen.
        """
        workspace = Workspace(
            name=name,
            slug=await self._unique_slug(slug or slugify(name)),
            default_country_code=default_country_code,
            timezone=timezone,
            currency=currency,
            seat_limit=seat_limit,
        )
        self._session.add(workspace)
        await self._session.flush()

        await self._registry.run_all(self._session, workspace)

        root = await self._session.execute(
            select(PermissionTemplate)
            .where(
                PermissionTemplate.workspace_id == workspace.id,
                PermissionTemplate.name == ROOT_TEMPLATE_NAME,
            )
            .limit(1)
        )
        root_template = root.scalar_one()

        membership = Membership(
            workspace_id=workspace.id,
            user_id=owner.id,
            template_id=root_template.id,
            has_license=True,
        )
        self._session.add(membership)
        await self._session.flush()

        return workspace, membership

    async def _unique_slug(self, base: str) -> str:
        """Append a discriminator rather than rejecting a duplicate name.

        Two customers may legitimately be called the same thing.
        """
        candidate = base
        while True:
            existing = await self._session.execute(
                select(Workspace.id).where(Workspace.slug == candidate).limit(1)
            )
            if existing.scalar_one_or_none() is None:
                return candidate
            candidate = f"{base}-{uuid.uuid4().hex[:6]}"


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """ASCII slug, safe for a URL and for `citext` comparison."""
    normalised = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", normalised.casefold()).strip("-")
    return slug[:60] or f"workspace-{uuid.uuid4().hex[:8]}"
