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

from app.fields.values import slugify_key
from app.models import (
    CallDisposition,
    LeadField,
    LostReason,
    Membership,
    PermissionTemplate,
    Stage,
    StageKind,
    User,
    Workspace,
)
from app.models.enums import PermissionGrant
from app.models.permission import TemplateFieldGrant
from app.services.fields import BUILTIN_FIELDS
from app.tenancy.features import DEFAULT_ENABLED

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
        "add_or_update": True,
        "create_from_whatsapp_and_calls": True,
        "manually_add_lead": True,
        "bulk_edit": True,
        "actions": True,
        "merge_leads": True,
        "search": True,
    },
    # `admin_access` grants everything in a group *including capabilities added
    # later* — but only for groups the template names at all. M7 shipped task
    # endpoints Root could not use until `tasks` appeared here, and M8 found
    # `automations`, `reports`, `calling`, `salesform`, `billings` and
    # `integrations` all still missing, with M8/M9/M10 endpoints queued behind
    # them. Root is `is_readonly`, so an admin cannot repair the omission from
    # the UI — a missing group is an unfixable dead feature.
    #
    # So Root names **every** access group. It is the workspace superuser; there
    # is no capability it should lack, and enumerating them here means no future
    # milestone has to remember this again.
    "permissions": {"admin_access": True},
    "team": {"admin_access": True},
    "tasks": {"admin_access": True},
    "reports": {"admin_access": True},
    "automations": {"admin_access": True},
    "calling": {"admin_access": True},
    "salesform": {"admin_access": True},
    "billings": {"admin_access": True},
    "integrations": {"admin_access": True},
}
_ADMIN_CAPS: dict[str, Any] = {
    "leads": {
        "admin_access": True,
        "add_or_update": True,
        "create_from_whatsapp_and_calls": True,
        "manually_add_lead": True,
        "bulk_edit": True,
        "actions": True,
        "merge_leads": True,
        "search": True,
    },
    "permissions": {"admin_access": True},
    "team": {"admin_access": True},
    "tasks": {"admin_access": True},
    "reports": {"admin_access": True},
    "automations": {"admin_access": True},
    "calling": {"admin_access": True},
    "salesform": {"admin_access": True},
    "integrations": {"admin_access": True},
    # Deliberately not `billings`. Admin is an editable template, so a workspace
    # that wants its admins in the billing screens can grant it; Root always has
    # it. Spending money is the one thing worth making someone opt into.
}
_MANAGER_CAPS: dict[str, Any] = {
    "leads": {
        "admin_access": False,
        "add_or_update": True,
        "create_from_whatsapp_and_calls": True,
        "manually_add_lead": True,
        "bulk_edit": True,
        "actions": True,
        "merge_leads": True,
        "search": True,
    },
    "team": {
        "view_members": True,
        "manage_availability": True,
    },
    "reports": {"view_reports": True, "view_team_reports": True, "view_leaderboard": True},
    "tasks": {
        "view_tasks": True,
        "create_tasks": True,
        "complete_tasks": True,
        "assign_tasks": True,
    },
    "calling": {"log_calls": True, "view_call_history": True},
    "view": {
        "lead": {"show_timeline": True, "show_tasks": True, "show_score": True},
        "dashboard": {"show_personal_dashboard": True, "show_team_dashboard": True},
        "leads_table": {
            "show_all_leads": True,
            "show_saved_filters": True,
            "show_column_picker": True,
        },
    },
}
_CALLER_CAPS: dict[str, Any] = {
    "leads": {
        "admin_access": False,
        # A telecaller's core job is working leads, so `add_or_update` is on.
        # It was absent from the M1 defaults only because the capability was
        # not yet named — M4 spec'd the group, M5 exposed the gap.
        "add_or_update": True,
        "create_from_whatsapp_and_calls": True,
        "manually_add_lead": True,
        "bulk_edit": False,
        "actions": True,
        "merge_leads": False,
        "search": True,
    },
    "calling": {"log_calls": True, "view_call_history": True},
    "tasks": {"view_tasks": True, "create_tasks": True, "complete_tasks": True},
    # A caller's *own* numbers, and nothing more: no team reports, no
    # leaderboard. Without this the View group below promises a personal
    # dashboard that every dashboard endpoint refuses to serve — a
    # contradiction that only became visible in M9, when something was finally
    # gated on `reports`. Visibility already confines what they see to their
    # own leads, so this grants a view of their own work, not of anyone else's.
    "reports": {"view_reports": True},
    "view": {
        "lead": {"show_timeline": True, "show_tasks": True},
        "dashboard": {"show_personal_dashboard": True},
        "leads_table": {"show_saved_filters": True},
    },
}
_MARKETING_CAPS: dict[str, Any] = {
    "leads": {
        "admin_access": False,
        # Marketing creates and enriches leads but does not work them, so it
        # gets `add_or_update` without `actions`.
        "add_or_update": True,
        "create_from_whatsapp_and_calls": False,
        "manually_add_lead": True,
        "bulk_edit": False,
        "actions": False,
        "merge_leads": False,
        "search": True,
    },
    "reports": {"view_reports": True},
    "view": {
        "dashboard": {"show_personal_dashboard": True},
        "leads_table": {"show_saved_filters": True},
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
        "field_grants",  # M4 - the default matrix over the built-in fields
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


async def _provision_lead_fields(session: AsyncSession, workspace: Workspace) -> None:
    """The four built-in lead fields, plus the identity and H1/H2 pointers.

    docs/01-data-model.md §7: Name (TEXT), Phone (PHONE), Email (EMAIL),
    Alternate Phone (PHONE); `identity_field_id` to Phone, H1 to Name, H2 to
    Phone.

    Deliberately the *only* fields a new workspace gets. No source, no status,
    no product — a new customer sees an empty, working CRM and builds their own
    schema. Seeding a taxonomy here is the #1 mistake CLAUDE.md names.
    """
    created: dict[str, LeadField] = {}
    for order, (label, field_type) in enumerate(BUILTIN_FIELDS):
        field = LeadField(
            workspace_id=workspace.id,
            key=slugify_key(label, taken=set(created)),
            label=label,
            field_type=field_type,
            is_builtin=True,
            sort_order=order,
            # A built-in is offered everywhere by default: these four are what
            # a quick-add form is for.
            show_in_import=True,
            show_in_quick_add=True,
        )
        session.add(field)
        created[field.key] = field

    await session.flush()

    workspace.identity_field_id = created["phone"].id
    workspace.primary_field_1_id = created["name"].id
    workspace.primary_field_2_id = created["phone"].id
    await session.flush()


provisioning_registry.register(ProvisioningStep(name="lead_fields", run=_provision_lead_fields))

#: docs/01-data-model.md §7. Deliberately structural: a starting point, one
#: step, and the two ways a deal ends. Every label here is expected to be
#: renamed by the customer on day one — they exist so the pipeline is usable
#: before it is configured, not as a suggested process.
_DEFAULT_STAGES: tuple[tuple[str, StageKind], ...] = (
    ("New", StageKind.INITIAL),
    ("Contacted", StageKind.ACTIVE),
    ("Won", StageKind.WON),
    ("Lost", StageKind.LOST),
)

#: §7. Generic commercial outcomes, not an industry's reasons. "Unknown" is the
#: default so a lead can always be marked lost without forcing a guess.
_DEFAULT_LOST_REASONS: tuple[tuple[str, bool], ...] = (
    ("Not interested", False),
    ("Budget", False),
    ("Competitor", False),
    ("No response", False),
    ("Unknown", True),
)

#: §7 and §3 — the 7 system dispositions, "Connected" default. These describe
#: what happened to a *phone call*, which is product vocabulary rather than a
#: customer's. They are `is_system`: archivable, never editable.
_SYSTEM_DISPOSITIONS: tuple[tuple[str, bool], ...] = (
    ("Connected", True),
    ("Number Busy", False),
    ("No Answer", False),
    ("Switched Off", False),
    ("Wrong Number", False),
    ("Call Later", False),
    ("Redialed", False),
)


async def _provision_stages(session: AsyncSession, workspace: Workspace) -> None:
    """One INITIAL, one ACTIVE, one WON, one LOST."""
    session.add_all(
        [
            Stage(workspace_id=workspace.id, kind=kind, label=label, sort_order=order)
            for order, (label, kind) in enumerate(_DEFAULT_STAGES)
        ]
    )
    await session.flush()


async def _provision_lost_reasons(session: AsyncSession, workspace: Workspace) -> None:
    session.add_all(
        [
            LostReason(
                workspace_id=workspace.id,
                label=label,
                is_default=is_default,
                sort_order=order,
            )
            for order, (label, is_default) in enumerate(_DEFAULT_LOST_REASONS)
        ]
    )
    await session.flush()


async def _provision_call_dispositions(session: AsyncSession, workspace: Workspace) -> None:
    session.add_all(
        [
            CallDisposition(
                workspace_id=workspace.id,
                label=label,
                is_default=is_default,
                is_system=True,
                sort_order=order,
            )
            for order, (label, is_default) in enumerate(_SYSTEM_DISPOSITIONS)
        ]
    )
    await session.flush()


provisioning_registry.register(ProvisioningStep(name="stages", run=_provision_stages))
provisioning_registry.register(ProvisioningStep(name="lost_reasons", run=_provision_lost_reasons))
provisioning_registry.register(
    ProvisioningStep(name="call_dispositions", run=_provision_call_dispositions)
)


#: Which grants each default template gets on the four built-in fields. Export
#: is deliberately absent from every non-admin template: the observed default is
#: `Export (0) None`, a data-exfiltration control worth matching.
_DEFAULT_TEMPLATE_GRANTS: dict[str, tuple[PermissionGrant, ...]] = {
    ROOT_TEMPLATE_NAME: (
        PermissionGrant.VIEW,
        PermissionGrant.EDIT,
        PermissionGrant.IMPORT,
        PermissionGrant.EXPORT,
    ),
    _ADMIN: (
        PermissionGrant.VIEW,
        PermissionGrant.EDIT,
        PermissionGrant.IMPORT,
        PermissionGrant.EXPORT,
    ),
    _MANAGER: (PermissionGrant.VIEW, PermissionGrant.EDIT, PermissionGrant.IMPORT),
    _CALLER: (PermissionGrant.VIEW, PermissionGrant.EDIT),
    _MARKETING: (PermissionGrant.VIEW, PermissionGrant.IMPORT),
}


async def _provision_field_grants(session: AsyncSession, workspace: Workspace) -> None:
    """Seed the field matrix for the five default templates.

    Runs last: it needs both `permission_templates` and `lead_fields` to exist.
    A workspace whose templates granted nothing would be one where nobody but an
    admin could read a lead, which is not a useful starting point — but nor is
    granting everything, so each template gets the narrowest set that makes its
    name true.
    """
    templates = await session.execute(
        select(PermissionTemplate).where(PermissionTemplate.workspace_id == workspace.id)
    )
    fields = await session.execute(select(LeadField).where(LeadField.workspace_id == workspace.id))
    field_ids = [f.id for f in fields.scalars().all()]

    session.add_all(
        [
            TemplateFieldGrant(
                workspace_id=workspace.id,
                template_id=template.id,
                field_id=field_id,
                grant=grant,
            )
            for template in templates.scalars().all()
            for grant in _DEFAULT_TEMPLATE_GRANTS.get(template.name, ())
            for field_id in field_ids
        ]
    )
    await session.flush()


provisioning_registry.register(ProvisioningStep(name="field_grants", run=_provision_field_grants))


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
            # §5 feature flags. A workspace opts into a module rather than
            # opting out of six, so only the two that a CRM is unusable
            # without start on.
            features=dict.fromkeys(sorted(DEFAULT_ENABLED), True),
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
