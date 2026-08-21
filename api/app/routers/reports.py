"""Report and dashboard endpoints (M9).

`docs/02-api-contract.md` §Dashboard and reports.

Everything here is a lead read, so everything here projects and everything here
applies the caller's visibility. A report that skipped either would be a way to
learn what a permission denied — the numbers *are* the data.

Note what is absent: there is no `/reports/sources`. `breakdown` takes a
`field_key` because which field means "source" is a per-workspace decision, and
a fixed endpoint would be the hardcoded-taxonomy mistake wearing a report's
clothing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.errors import api_error
from app.schemas.report import (
    BucketRead,
    DashboardCreate,
    DashboardRead,
    DashboardUpdate,
    FollowUpCounts,
    LeaderboardRowRead,
    WidgetSpec,
)
from app.services.dashboards import WIDGET_CATALOGUE, DashboardService
from app.services.reports import DateRange, ReportService
from app.tenancy.scoping import WorkspaceScope, require_workspace

router = APIRouter(tags=["reports"])


def _require(scope: WorkspaceScope, group: str, capability: str) -> None:
    if not scope.capability(group, capability):
        raise api_error(
            403,
            "insufficient_permissions",
            f"This permission template does not allow: {capability.replace('_', ' ')}",
        )


async def _reports(scope: WorkspaceScope) -> ReportService:
    """Built with the caller's grants and visibility already bound."""
    return ReportService(
        scope.session,
        workspace=scope.workspace,
        grants=await scope.field_grants(),
        visible_membership_ids=scope.visible_membership_ids,
        sees_all=scope.sees_all_members,
    )


def _dashboards(scope: WorkspaceScope) -> DashboardService:
    return DashboardService(
        scope.session,
        membership_id=scope.membership_id,
        template_id=scope.membership.template_id,
    )


def _window(scope: WorkspaceScope, start: dt.date | None, end: dt.date | None) -> DateRange:
    """A range in the workspace's timezone. See `DateRange.of`."""
    return DateRange.of(start, end, timezone=scope.workspace.timezone)


DateFrom = Annotated[dt.date | None, Query(alias="from")]
DateTo = Annotated[dt.date | None, Query(alias="to")]

# --- dashboard aggregates ----------------------------------------------------


@router.get("/dashboard/follow-ups", response_model=FollowUpCounts, summary="What needs doing")
async def follow_ups(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    from_: DateFrom = None,
    to: DateTo = None,
) -> FollowUpCounts:
    """Deliberately about *now*, not the window.

    An overdue follow-up is overdue today whatever range the operator happens to
    be looking at; burying it inside a date filter is how it gets missed.
    """
    _require(scope, "reports", "view_reports")
    counts = await (await _reports(scope)).follow_ups(window=_window(scope, from_, to))
    return FollowUpCounts(**counts)


@router.get(
    "/dashboard/leads-by-stage",
    response_model=list[BucketRead],
    summary="Leads per pipeline stage",
)
async def leads_by_stage(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    from_: DateFrom = None,
    to: DateTo = None,
) -> list[BucketRead]:
    _require(scope, "reports", "view_reports")
    buckets = await (await _reports(scope)).leads_by_stage(_window(scope, from_, to))
    return [BucketRead(key=b.key, label=b.label, count=b.count) for b in buckets]


@router.get(
    "/dashboard/filter-stats",
    response_model=list[BucketRead],
    summary="How many leads each saved filter matches",
)
async def filter_stats(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    limit: Annotated[int, Query(ge=1, le=10)] = 6,
) -> list[BucketRead]:
    """Badge counts for the saved-filter sidebar.

    Capped, and low. Each filter is a separate count over the whole lead table,
    and a saved filter may carry a history predicate — so this is the one report
    whose cost scales with how many *questions* the workspace has saved rather
    than how much data it holds. A workspace with forty saved filters would turn
    one sidebar into forty aggregate scans; six is a sidebar, not a report.
    """
    _require(scope, "reports", "view_reports")

    from app.services.leads import LeadService
    from app.services.views import ViewService

    views = ViewService(
        scope.session,
        membership_id=scope.membership_id,
        template_id=scope.membership.template_id,
        is_admin=scope.is_workspace_admin,
    )
    leads = LeadService(
        scope.session,
        workspace=scope.workspace,
        projection=await scope.projection(),
        write_filter=await scope.write_filter(),
        actor_id=scope.membership_id,
        visible_membership_ids=scope.visible_membership_ids,
        sees_all=scope.sees_all_members,
    )

    stats: list[BucketRead] = []
    for saved in (await views.list_filters())[:limit]:
        clause = await leads.compile_filter(_as_node(saved.definition))
        total, _ = await leads.count_by_stage(clause)
        stats.append(BucketRead(key=str(saved.id), label=saved.name, count=total))
    return stats


def _as_node(definition: Any) -> Any:
    """A stored filter document as a DSL node, or None for an empty one."""
    from pydantic import TypeAdapter

    from app.filters.dsl import FilterNode

    if not definition:
        return None
    return TypeAdapter(FilterNode).validate_python(definition)


# --- reports -----------------------------------------------------------------


@router.get("/reports/funnel", response_model=list[BucketRead], summary="Funnel")
async def funnel(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    from_: DateFrom = None,
    to: DateTo = None,
) -> list[BucketRead]:
    """Stage counts in pipeline order, won and lost last."""
    _require(scope, "reports", "view_reports")
    buckets = await (await _reports(scope)).funnel(_window(scope, from_, to))
    return [BucketRead(key=b.key, label=b.label, count=b.count) for b in buckets]


@router.get("/reports/breakdown", response_model=list[BucketRead], summary="Group by any field")
async def breakdown(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    field_key: Annotated[str, Query(max_length=64)],
    from_: DateFrom = None,
    to: DateTo = None,
    assignee_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[BucketRead]:
    """The report that replaces a dozen.

    "Leads by source", "by course", "by city" are all this with a different
    `field_key`. A field the caller cannot view reads as `unknown_field` —
    identical to one that does not exist, so a denial cannot be distinguished
    from an absence.
    """
    _require(scope, "reports", "view_reports")
    buckets = await (await _reports(scope)).breakdown(
        field_key=field_key,
        window=_window(scope, from_, to),
        assignee_id=assignee_id,
    )
    return [BucketRead(key=b.key, label=b.label, count=b.count) for b in buckets]


@router.get("/reports/activity", response_model=dict[str, int], summary="Activity")
async def activity(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    from_: DateFrom = None,
    to: DateTo = None,
) -> dict[str, int]:
    _require(scope, "reports", "view_reports")
    return await (await _reports(scope)).activity(window=_window(scope, from_, to))


@router.get(
    "/reports/leaderboard",
    response_model=list[LeaderboardRowRead],
    summary="Per-member totals",
)
async def leaderboard(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    from_: DateFrom = None,
    to: DateTo = None,
) -> list[LeaderboardRowRead]:
    """Honours `workspaces.leaderboard_metrics`.

    A team that ranks on calls made and one that ranks on deals won are both
    right about their own business, so the product does not choose.
    """
    _require(scope, "reports", "view_leaderboard")
    rows = await (await _reports(scope)).leaderboard(window=_window(scope, from_, to))
    return [
        LeaderboardRowRead(membership_id=row.membership_id, name=row.name, metrics=row.metrics)
        for row in rows
    ]


# --- dashboards --------------------------------------------------------------


@router.get("/dashboards/widgets", response_model=list[WidgetSpec], summary="Widget catalogue")
async def widget_catalogue(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[WidgetSpec]:
    """Each widget carries its own config schema.

    So the editor can render a form for a widget it has never heard of — the
    same pattern `/settings/field-types` established in M2. Declared before
    `/dashboards/{id}` so "widgets" is not read as an id.
    """
    _require(scope, "reports", "view_reports")
    return [WidgetSpec(**entry) for entry in WIDGET_CATALOGUE]


@router.get("/dashboards", response_model=list[DashboardRead], summary="Dashboards")
async def list_dashboards(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[DashboardRead]:
    """Personal, shared and role-bound, in one list."""
    _require(scope, "reports", "view_reports")
    rows = await _dashboards(scope).visible()
    return [DashboardRead.model_validate(row) for row in rows]


@router.post(
    "/dashboards",
    response_model=DashboardRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a dashboard",
)
async def create_dashboard(
    body: DashboardCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> DashboardRead:
    _require(scope, "reports", "view_reports")
    if (body.shared or body.template_id is not None) and not scope.capability(
        "reports", "view_team_reports"
    ):
        # Making a dashboard for other people is a different act from making one
        # for yourself, so it needs the capability that covers other people.
        raise api_error(
            403,
            "insufficient_permissions",
            "This permission template does not allow sharing dashboards",
        )
    dashboard = await _dashboards(scope).create(
        name=body.name,
        layout=body.layout,
        shared=body.shared,
        template_id=body.template_id,
    )
    await scope.session.commit()
    return DashboardRead.model_validate(dashboard)


@router.get("/dashboards/{dashboard_id}", response_model=DashboardRead, summary="One dashboard")
async def get_dashboard(
    dashboard_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> DashboardRead:
    _require(scope, "reports", "view_reports")
    return DashboardRead.model_validate(await _dashboards(scope).get(dashboard_id))


@router.patch(
    "/dashboards/{dashboard_id}",
    response_model=DashboardRead,
    summary="Update a dashboard",
)
async def update_dashboard(
    dashboard_id: uuid.UUID,
    body: DashboardUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> DashboardRead:
    """Seeing a shared dashboard is not permission to rewrite it."""
    _require(scope, "reports", "view_reports")
    dashboard = await _dashboards(scope).update(dashboard_id, **body.model_dump(exclude_unset=True))
    await scope.session.commit()
    return DashboardRead.model_validate(dashboard)


@router.delete(
    "/dashboards/{dashboard_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a dashboard",
)
async def archive_dashboard(
    dashboard_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> None:
    _require(scope, "reports", "view_reports")
    await _dashboards(scope).archive(dashboard_id)
    await scope.session.commit()


@router.put(
    "/dashboards/{dashboard_id}/default",
    response_model=DashboardRead,
    summary="Make this the caller's landing dashboard",
)
async def set_default_dashboard(
    dashboard_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> DashboardRead:
    """Per caller, not per dashboard — otherwise choosing a shared dashboard as
    your landing page would change everyone else's."""
    _require(scope, "reports", "view_reports")
    dashboard = await _dashboards(scope).set_default(dashboard_id)
    await scope.session.commit()
    return DashboardRead.model_validate(dashboard)
