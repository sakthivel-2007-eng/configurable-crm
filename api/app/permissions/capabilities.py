"""The permission capability model (M4).

`docs/03-configuration-model.md` §6.2 names 10 Access groups and 3 View groups
but only documents the *contents* of Access -> Leads; §8 marks the other nine
"Not inspected". PROMPTS.md M4 is explicit about what to do:

> Where the source system's contents are unknown, propose a set and flag it for
> review.

So: **Leads is observed.** Everything else is proposed, and every proposed group
carries a `# PROPOSED` marker naming what it was derived from. They are
deliberately conservative — each is a capability the rest of this codebase
already needs, not a speculative feature list.

`docs/01-data-model.md` §3.5 says this JSONB blob must be validated: "a JSONB
blob nobody validates becomes a JSONB blob nobody understands." These models are
that validation.

**Deny by default is the whole design.** Every flag defaults to `False`, and a
group absent from a stored blob validates to all-False rather than raising —
a template written before a group existed must not suddenly grant it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ACCESS_GROUPS",
    "PROPOSED_GROUPS",
    "VIEW_GROUPS",
    "Capabilities",
]


class _Group(BaseModel):
    """Base for every capability group.

    `extra="ignore"` so a template stored by a newer version, or one carrying a
    key that has since been removed, still loads. Silently dropping an unknown
    grant is the safe direction: the alternative is a workspace whose templates
    stop validating after a deploy.
    """

    model_config = ConfigDict(extra="ignore")

    #: Master switch for the group. When true the holder has every capability
    #: in it, including ones added later — that is what "admin" means and why
    #: it is worth having as a distinct flag rather than "all boxes ticked".
    admin_access: bool = False


class LeadsAccess(_Group):
    """**Observed** — §6.3 lists these exactly."""

    create_from_whatsapp_and_calls: bool = False
    add_or_update: bool = False
    manually_add_lead: bool = False
    bulk_edit: bool = False
    actions: bool = False
    merge_leads: bool = False
    search: bool = False


class TeamAccess(_Group):
    """PROPOSED — derived from the M1 member-lifecycle endpoints that exist."""

    view_members: bool = False
    invite_members: bool = False
    edit_members: bool = False
    deactivate_members: bool = False
    manage_licenses: bool = False
    manage_availability: bool = False
    manage_hierarchy: bool = False
    #: M8. Groups are a distribution target and an M9 report segment, so
    #: they sit with the team rather than with the rules that read them.
    manage_sales_groups: bool = False


class PermissionsAccess(_Group):
    """PROPOSED — derived from the template endpoints in this milestone."""

    view_templates: bool = False
    create_templates: bool = False
    edit_templates: bool = False
    assign_templates: bool = False
    edit_field_grants: bool = False


class CallingAccess(_Group):
    """PROPOSED — derived from M3's dispositions and M5's manual call logging.

    No telephony capabilities: there is no dialer in v1, so there is nothing to
    grant. `call_recording` deliberately absent for the same reason.
    """

    log_calls: bool = False
    view_call_history: bool = False
    manage_dispositions: bool = False


class ReportsAccess(_Group):
    """PROPOSED — derived from the M9 report endpoints in the API contract."""

    view_reports: bool = False
    view_leaderboard: bool = False
    export_reports: bool = False
    view_team_reports: bool = False
    schedule_reports: bool = False


class AutomationsAccess(_Group):
    """PROPOSED — v1 ships webhooks; the workflow engine is v2."""

    view_automations: bool = False
    #: M8. Editing a rule redirects where every future lead lands, so it is
    #: separate from merely viewing the automation list.
    manage_assignment_rules: bool = False
    #: M8. Redistributing existing leads takes work off one rep and gives it
    #: to another. Distinct from creating the rule that would have done it.
    distribute_leads: bool = False
    manage_webhooks: bool = False
    manage_api_keys: bool = False
    view_intake_log: bool = False


class TasksAccess(_Group):
    """PROPOSED — derived from the M7 task endpoints."""

    view_tasks: bool = False
    create_tasks: bool = False
    complete_tasks: bool = False
    assign_tasks: bool = False
    bulk_upload_tasks: bool = False


class SalesformAccess(_Group):
    """PROPOSED — Salesform is v1.5; the group exists so a template written now
    does not need migrating when it lands."""

    view_forms: bool = False
    manage_forms: bool = False


class BillingsAccess(_Group):
    """PROPOSED — billing is explicitly an anti-requirement in v1
    (docs/01-data-model.md §9). Kept as a named group only."""

    view_billing: bool = False
    manage_billing: bool = False


class IntegrationsAccess(_Group):
    """PROPOSED — derived from M10's outbound and intake surfaces."""

    view_integrations: bool = False
    manage_integrations: bool = False
    manage_embedded_apps: bool = False


class LeadView(_Group):
    """PROPOSED — what the lead detail overlay exposes (§6.2 "View -> Lead")."""

    show_timeline: bool = False
    show_tasks: bool = False
    show_score: bool = False
    show_source_attribution: bool = False
    show_assignment_history: bool = False


class DashboardView(_Group):
    """PROPOSED — derived from the M9 dashboard widgets."""

    show_personal_dashboard: bool = False
    show_team_dashboard: bool = False
    show_leaderboard: bool = False


class LeadsTableView(_Group):
    """PROPOSED — derived from the M6 list surface."""

    show_all_leads: bool = False
    show_saved_filters: bool = False
    manage_shared_filters: bool = False
    show_column_picker: bool = False
    show_export_button: bool = False


class ViewGroups(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lead: LeadView = Field(default_factory=LeadView)
    dashboard: DashboardView = Field(default_factory=DashboardView)
    leads_table: LeadsTableView = Field(default_factory=LeadsTableView)


class Capabilities(BaseModel):
    """The validated shape of `permission_templates.capabilities`.

    Every group defaults to an all-False instance, so a blob missing a group
    grants nothing from it. That is the deny-by-default rule expressed in the
    type rather than in a comment.
    """

    model_config = ConfigDict(extra="ignore")

    leads: LeadsAccess = Field(default_factory=LeadsAccess)
    salesform: SalesformAccess = Field(default_factory=SalesformAccess)
    team: TeamAccess = Field(default_factory=TeamAccess)
    permissions: PermissionsAccess = Field(default_factory=PermissionsAccess)
    calling: CallingAccess = Field(default_factory=CallingAccess)
    reports: ReportsAccess = Field(default_factory=ReportsAccess)
    automations: AutomationsAccess = Field(default_factory=AutomationsAccess)
    tasks: TasksAccess = Field(default_factory=TasksAccess)
    billings: BillingsAccess = Field(default_factory=BillingsAccess)
    integrations: IntegrationsAccess = Field(default_factory=IntegrationsAccess)
    view: ViewGroups = Field(default_factory=ViewGroups)

    def allows(self, group: str, name: str) -> bool:
        """Whether one capability is granted.

        `admin_access` on a group grants everything in it — including
        capabilities added in a later release, which is the point of having it
        as a flag rather than as "every box ticked".
        """
        section = getattr(self, group, None)
        if section is None:
            section = getattr(self.view, group, None)
        if not isinstance(section, _Group):
            return False
        if section.admin_access:
            return True
        return bool(getattr(section, name, False))

    @classmethod
    def from_stored(cls, blob: dict[str, Any] | None) -> Capabilities:
        """Load a stored blob, tolerating anything unexpected in it.

        Never raises. A template that fails to load is a member who cannot log
        in, and a validation error in a settings blob is not worth that.
        """
        try:
            return cls.model_validate(blob or {})
        except Exception:
            return cls()


#: The 10 Access groups, in the order §6.2 lists them.
ACCESS_GROUPS: tuple[str, ...] = (
    "leads",
    "salesform",
    "team",
    "permissions",
    "calling",
    "reports",
    "automations",
    "tasks",
    "billings",
    "integrations",
)

#: The 3 View groups.
VIEW_GROUPS: tuple[str, ...] = ("lead", "dashboard", "leads_table")

#: Groups whose contents this codebase proposed rather than observed. Surfaced
#: through the API so the settings UI can mark them for review, and asserted in
#: tests so the list cannot quietly grow.
PROPOSED_GROUPS: frozenset[str] = frozenset(
    {
        "salesform",
        "team",
        "permissions",
        "calling",
        "reports",
        "automations",
        "tasks",
        "billings",
        "integrations",
        "lead",
        "dashboard",
        "leads_table",
    }
)
