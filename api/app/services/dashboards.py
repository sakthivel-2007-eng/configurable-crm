"""Custom and role-bound dashboards (M9).

`docs/02-api-contract.md` §Custom dashboards.

**The widget catalogue is served, not hardcoded in the frontend.** Each entry
carries its own config schema, so the dashboard editor can render a form for a
widget it has never heard of. That is the same pattern `/settings/field-types`
established in M2 and the M2 UI already consumes — copying it means the frontend
does not need a release to learn about a new widget.

**A widget's `field_key` is the customer's choice, every time.** The catalogue
names *kinds* of chart, never subjects. There is no "leads by source" widget,
because which field means source is a per-workspace decision — the same reason
`/reports/breakdown` is parameterised.

**Three ways to reach a dashboard, one query.** Personal (`owner_id` = you),
shared (`owner_id` null), and role-bound (`template_id` = your template). A
member sees the union, which is what makes "give the callers this dashboard"
a single admin action rather than one per caller.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_

from app.errors import api_error
from app.models import Dashboard, PermissionTemplate
from app.tenancy.session import ScopedSession

__all__ = ["MAX_WIDGETS", "WIDGET_CATALOGUE", "DashboardService"]

#: A screen, not a report suite. Beyond this nothing is readable and every
#: widget is a query.
MAX_WIDGETS = 20

#: The widget catalogue. Kinds of chart — never subjects.
#:
#: `config` is a small JSON-schema-ish description the editor renders a form
#: from. `field_key` options are resolved by the frontend from the workspace's
#: own fields, which is what keeps a customer's vocabulary out of this list.
WIDGET_CATALOGUE: tuple[dict[str, Any], ...] = (
    {
        "key": "follow_ups",
        "label": "Follow-ups",
        "description": "Late, upcoming and never-contacted counts.",
        "shape": "stats",
        "config": {},
        "default_size": {"w": 4, "h": 2},
    },
    {
        "key": "leads_by_stage",
        "label": "Leads by stage",
        "description": "One bar per pipeline stage.",
        "shape": "bar",
        "config": {},
        "default_size": {"w": 6, "h": 4},
    },
    {
        "key": "funnel",
        "label": "Funnel",
        "description": "Stages in pipeline order, won and lost last.",
        "shape": "funnel",
        "config": {},
        "default_size": {"w": 6, "h": 4},
    },
    {
        "key": "breakdown",
        "label": "Breakdown by field",
        "description": "Group leads by any field you can view.",
        "shape": "bar",
        # The one widget that needs configuring, and the reason the catalogue
        # carries schemas at all.
        "config": {
            "field_key": {
                "type": "field",
                "label": "Group by",
                "required": True,
                "help": "Any lead field — which one means 'source' is your call.",
            }
        },
        "default_size": {"w": 6, "h": 4},
    },
    {
        "key": "activity",
        "label": "Activity",
        "description": "Timeline volume by action kind.",
        "shape": "bar",
        "config": {},
        "default_size": {"w": 6, "h": 4},
    },
    {
        "key": "leaderboard",
        "label": "Leaderboard",
        "description": "Per-member totals, using this workspace's metrics.",
        "shape": "table",
        "config": {},
        "default_size": {"w": 6, "h": 5},
    },
)

_WIDGET_KEYS = frozenset(entry["key"] for entry in WIDGET_CATALOGUE)


class DashboardService:
    """CRUD, plus the visibility union that makes role-binding work."""

    def __init__(
        self,
        session: ScopedSession,
        *,
        membership_id: uuid.UUID,
        template_id: uuid.UUID,
    ) -> None:
        self._session = session
        self._membership_id = membership_id
        self._template_id = template_id

    async def visible(self) -> list[Dashboard]:
        """Personal, shared, and role-bound — in one query."""
        rows = await self._session.execute(
            self._session.select(Dashboard)
            .where(
                Dashboard.is_archived.is_(False),
                or_(
                    Dashboard.owner_id == self._membership_id,
                    Dashboard.owner_id.is_(None),
                    Dashboard.template_id == self._template_id,
                ),
            )
            .order_by(Dashboard.name)
        )
        return list(rows.scalars().all())

    async def get(self, dashboard_id: uuid.UUID) -> Dashboard:
        dashboard = await self._session.get(Dashboard, dashboard_id)
        if dashboard is None or not self._can_see(dashboard):
            # Same 404 for "does not exist" and "not yours" — a 403 would
            # confirm that somebody else's dashboard exists.
            raise api_error(404, "dashboard_not_found", "No such dashboard")
        return dashboard

    def _can_see(self, dashboard: Dashboard) -> bool:
        return (
            dashboard.owner_id == self._membership_id
            or dashboard.owner_id is None
            or dashboard.template_id == self._template_id
        )

    def _assert_owns(self, dashboard: Dashboard) -> None:
        """Seeing a shared dashboard is not permission to rewrite it."""
        if dashboard.owner_id != self._membership_id:
            raise api_error(
                403,
                "not_your_dashboard",
                "Only the owner can change this dashboard",
            )

    async def create(
        self,
        *,
        name: str,
        layout: Sequence[dict[str, Any]],
        shared: bool = False,
        template_id: uuid.UUID | None = None,
    ) -> Dashboard:
        self._validate_layout(layout)
        if template_id is not None:
            await self._assert_template(template_id)
        owner_id = None if shared else self._membership_id

        clash = await self._session.execute(
            self._session.select(Dashboard).where(
                func.lower(Dashboard.name) == name.lower(),
                Dashboard.owner_id.is_(None)
                if owner_id is None
                else Dashboard.owner_id == owner_id,
            )
        )
        if clash.scalars().first() is not None:
            raise api_error(409, "duplicate_dashboard", f"You already have {name!r}")

        dashboard = Dashboard(
            name=name,
            owner_id=owner_id,
            template_id=template_id,
            layout=list(layout),
        )
        self._session.add(dashboard)
        await self._session.flush()
        return dashboard

    async def update(self, dashboard_id: uuid.UUID, **changes: Any) -> Dashboard:
        dashboard = await self.get(dashboard_id)
        self._assert_owns(dashboard)

        if changes.get("layout") is not None:
            self._validate_layout(changes["layout"])
            dashboard.layout = list(changes["layout"])
        if changes.get("name") is not None:
            dashboard.name = changes["name"]
        if "template_id" in changes:
            if changes["template_id"] is not None:
                await self._assert_template(changes["template_id"])
            dashboard.template_id = changes["template_id"]
        await self._session.flush()
        return dashboard

    async def archive(self, dashboard_id: uuid.UUID) -> None:
        """Archive rather than delete (rule 13).

        A shared dashboard somebody has built their morning around should not
        vanish from under them because its author tidied up.
        """
        dashboard = await self.get(dashboard_id)
        self._assert_owns(dashboard)
        dashboard.is_archived = True
        await self._session.flush()

    async def set_default(self, dashboard_id: uuid.UUID) -> Dashboard:
        """The caller's landing dashboard.

        Per *caller*, not per dashboard: clearing the flag on everything they
        can see would unset a shared dashboard for the whole workspace.
        """
        dashboard = await self.get(dashboard_id)
        mine = await self._session.execute(
            self._session.select(Dashboard).where(
                Dashboard.owner_id == self._membership_id, Dashboard.is_default.is_(True)
            )
        )
        for existing in mine.scalars().all():
            existing.is_default = False
        dashboard.is_default = True
        await self._session.flush()
        return dashboard

    # --- validation --------------------------------------------------------

    def _validate_layout(self, layout: Sequence[dict[str, Any]]) -> None:
        """Refuse a layout naming a widget that does not exist.

        Stored unvalidated, it would render as a blank tile forever with no
        indication of why — and the person who could fix it is not the person
        who would see it.
        """
        if len(layout) > MAX_WIDGETS:
            raise api_error(422, "too_many_widgets", f"At most {MAX_WIDGETS} widgets")
        for index, item in enumerate(layout):
            widget = item.get("widget")
            if widget not in _WIDGET_KEYS:
                raise api_error(
                    422,
                    "unknown_widget",
                    f"Widget {widget!r} at position {index} is not in the catalogue",
                )
            if widget == "breakdown" and not item.get("config", {}).get("field_key"):
                raise api_error(
                    422,
                    "widget_config_required",
                    "A breakdown widget needs a `field_key` to group by",
                )

    async def _assert_template(self, template_id: uuid.UUID) -> None:
        template = await self._session.get(PermissionTemplate, template_id)
        if template is None:
            raise api_error(422, "unknown_template", "No such permission template here")
