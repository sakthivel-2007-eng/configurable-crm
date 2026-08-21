"""Reports and dashboards (M9).

Three properties carry this milestone:

- **there is no sources report**, and `breakdown` is parameterised so there
  never has to be one
- **a report is a lead read**, so it projects and it applies visibility — the
  numbers *are* the data, and a report that skipped either would be a way to
  learn what a permission denied
- **the drill-through agrees with the chart**: click a cell showing N and the
  list contains exactly N, which is the milestone's own acceptance check
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import WorkspaceFixture, add_member, build_workspace, login

from app.auth.passwords import PasswordHasherService

pytestmark = pytest.mark.integration


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


@pytest.fixture
async def ws(
    db_session: AsyncSession, hasher: PasswordHasherService, api: AsyncClient
) -> WorkspaceFixture:
    fixture = await build_workspace(
        db_session, hasher, name="Reports Co", owner_email="reports-owner@example.com"
    )
    await login(api, fixture.owner)
    return fixture


async def _field(api: AsyncClient, ws: WorkspaceFixture, label: str) -> str:
    response = await api.post(
        ws.path("/settings/lead-fields"),
        headers=ws.owner.auth,
        json={"label": label, "field_type": "TEXT"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["key"])


async def _lead(
    api: AsyncClient, ws: WorkspaceFixture, phone: str, values: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await api.post(
        ws.path("/leads"),
        headers=ws.owner.auth,
        json={"values": {"name": "Lead", "phone": phone, **(values or {})}},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- the taxonomy guard ------------------------------------------------------


def test_there_is_no_fixed_sources_report(wired_app: FastAPI) -> None:
    """The mistake this milestone is most likely to make.

    A `/reports/sources` endpoint would be the hardcoded-taxonomy error in a new
    costume: *which* field means "source" is a per-workspace decision. This
    asserts against the app's own schema so the reminder arrives with the route,
    not after an audit.
    """
    paths = set(wired_app.openapi()["paths"])
    forbidden = {"source", "course", "status", "product", "enquiry", "campaign"}
    for path in paths:
        if "/reports/" not in path:
            continue
        leaf = path.rsplit("/", 1)[-1].lower()
        assert not (forbidden & set(leaf.replace("-", "_").split("_"))), (
            f"{path} names a business concept — parameterise it like /reports/breakdown"
        )


async def test_breakdown_groups_by_whatever_field_it_is_given(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """One endpoint replaces a dozen."""
    key = await _field(api, ws, "Enquiry channel")
    for index, channel in enumerate(["Walk-in", "Walk-in", "Phone"]):
        await _lead(api, ws, f"+1999555100{index}", {key: channel})

    response = await api.get(
        ws.path("/reports/breakdown"), headers=ws.owner.auth, params={"field_key": key}
    )
    assert response.status_code == 200, response.text
    counts = {row["label"]: row["count"] for row in response.json()}
    assert counts["Walk-in"] == 2
    assert counts["Phone"] == 1


async def test_breakdown_names_leads_with_no_value(api: AsyncClient, ws: WorkspaceFixture) -> None:
    """An empty bucket is a real answer, not a gap in the chart."""
    key = await _field(api, ws, "Region")
    await _lead(api, ws, "+19995551010", {key: "North"})
    await _lead(api, ws, "+19995551011")

    response = await api.get(
        ws.path("/reports/breakdown"), headers=ws.owner.auth, params={"field_key": key}
    )
    labels = {row["label"] for row in response.json()}
    assert "(not set)" in labels


# --- permissions -------------------------------------------------------------


async def test_a_field_the_caller_cannot_view_reads_as_unknown(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    """Identical to a field that does not exist.

    A distinct "you may not group by that" would confirm the field exists, which
    is the thing the View denial is hiding. The filter compiler makes the same
    choice for the same reason.
    """
    key = await _field(api, ws, "Salary band")
    caller = await add_member(
        db_session,
        hasher,
        ws,
        key="caller",
        email="reports-caller@example.com",
        template_name="Caller",
    )
    await login(api, caller)

    response = await api.get(
        ws.path("/reports/breakdown"), headers=caller.auth, params={"field_key": key}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_field"

    absent = await api.get(
        ws.path("/reports/breakdown"),
        headers=caller.auth,
        params={"field_key": "no_such_field_at_all"},
    )
    assert absent.status_code == 422
    assert absent.json()["detail"]["code"] == response.json()["detail"]["code"]
    # The two messages differ only by the key the caller themselves sent, so
    # they carry nothing an observer did not already know. That is the property
    # — not byte-equality, which would be impossible while the message is
    # useful at all.
    assert absent.json()["detail"]["message"].replace(
        "no_such_field_at_all", "X"
    ) == response.json()["detail"]["message"].replace(key, "X")


async def test_a_caller_sees_only_their_own_numbers(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    """The numbers are the data.

    A report that skipped the visibility rule would let a caller total up their
    colleagues' pipeline by asking a different endpoint from the one that
    refuses to list it.
    """
    caller = await add_member(
        db_session,
        hasher,
        ws,
        key="own",
        email="reports-own@example.com",
        template_name="Caller",
    )
    await login(api, caller)

    mine = await _lead(api, ws, "+19995552001")
    await api.patch(
        ws.path(f"/leads/{mine['id']}"),
        headers=ws.owner.auth,
        json={"assignee_id": str(caller.membership.id)},
    )
    await _lead(api, ws, "+19995552002")  # somebody else's

    stages = await api.get(ws.path("/dashboard/leads-by-stage"), headers=caller.auth)
    assert stages.status_code == 200, stages.text
    assert sum(row["count"] for row in stages.json()) == 1

    as_owner = await api.get(ws.path("/dashboard/leads-by-stage"), headers=ws.owner.auth)
    assert sum(row["count"] for row in as_owner.json()) == 2


async def test_the_leaderboard_needs_its_own_capability(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    marketing = await add_member(
        db_session,
        hasher,
        ws,
        key="marketing",
        email="reports-marketing@example.com",
        template_name="Marketing",
    )
    await login(api, marketing)
    response = await api.get(ws.path("/reports/leaderboard"), headers=marketing.auth)
    assert response.status_code == 403


# --- the leaderboard ---------------------------------------------------------


async def test_the_leaderboard_honours_the_workspace_s_chosen_metrics(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession
) -> None:
    """A team ranking on calls and one ranking on deals are both right.

    So the product does not choose — `leaderboard_metrics` does, and it already
    existed as a column before this milestone.
    """
    from sqlalchemy import update

    from app.models import Workspace

    baseline = await api.get(ws.path("/reports/leaderboard"), headers=ws.owner.auth)
    assert baseline.status_code == 200, baseline.text
    assert all("average_rating" not in row["metrics"] for row in baseline.json())

    await db_session.execute(
        update(Workspace)
        .where(Workspace.id == ws.workspace.id)
        .values(leaderboard_metrics={"stage": True, "rating": True})
    )
    await db_session.commit()

    lead = await _lead(api, ws, "+19995553001")
    await api.patch(
        ws.path(f"/leads/{lead['id']}"),
        headers=ws.owner.auth,
        json={"assignee_id": str(ws.owner.membership.id), "rating": 4},
    )

    after = await api.get(ws.path("/reports/leaderboard"), headers=ws.owner.auth)
    rows = {row["membership_id"]: row for row in after.json()}
    assert rows[str(ws.owner.membership.id)]["metrics"].get("average_rating") == 4


# --- the drill-through -------------------------------------------------------


async def test_a_chart_cell_and_the_list_behind_it_agree(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """The milestone's own acceptance check.

    Click a cell showing N and the list must contain exactly N. A chart that
    disagrees with the list it drills into is worse than no chart, because it
    is believed.
    """
    key = await _field(api, ws, "Neighbourhood")
    for index in range(3):
        await _lead(api, ws, f"+1999555400{index}", {key: "Riverside"})
    await _lead(api, ws, "+19995554009", {key: "Hilltop"})

    chart = await api.get(
        ws.path("/reports/breakdown"), headers=ws.owner.auth, params={"field_key": key}
    )
    riverside = next(row for row in chart.json() if row["label"] == "Riverside")
    assert riverside["count"] == 3

    drilled = await api.post(
        ws.path("/leads/search"),
        headers=ws.owner.auth,
        json={
            "filter": {
                "type": "group",
                "op": "AND",
                "children": [{"type": "field", "key": key, "op": "eq", "value": "Riverside"}],
            },
            "limit": 50,
        },
    )
    assert drilled.status_code == 200, drilled.text
    assert drilled.json()["total"] == riverside["count"]


# --- dashboards --------------------------------------------------------------


async def test_the_widget_catalogue_carries_each_widget_s_config_schema(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """So the editor can render a form for a widget it has never heard of."""
    response = await api.get(ws.path("/dashboards/widgets"), headers=ws.owner.auth)
    assert response.status_code == 200, response.text
    widgets = {entry["key"]: entry for entry in response.json()}

    assert "breakdown" in widgets
    assert widgets["breakdown"]["config"]["field_key"]["type"] == "field"
    # And the catalogue names kinds of chart, never subjects.
    for key in widgets:
        assert key not in {"leads_by_source", "leads_by_course", "leads_by_status"}


async def test_a_layout_naming_an_unknown_widget_is_refused(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """Stored unvalidated it would render as a blank tile forever.

    And the person who could fix it is not the person who would see it.
    """
    response = await api.post(
        ws.path("/dashboards"),
        headers=ws.owner.auth,
        json={
            "name": "Broken",
            "layout": [{"widget": "leads_by_astrology", "x": 0, "y": 0, "w": 6, "h": 4}],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_widget"


async def test_a_breakdown_widget_must_say_what_to_group_by(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    response = await api.post(
        ws.path("/dashboards"),
        headers=ws.owner.auth,
        json={
            "name": "Unconfigured",
            "layout": [{"widget": "breakdown", "x": 0, "y": 0, "w": 6, "h": 4}],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "widget_config_required"


async def test_a_role_bound_dashboard_reaches_every_member_on_that_template(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    """The milestone's acceptance check: build it as admin, see it as a caller.

    One admin action rather than one per person, which is the whole point of
    binding to a template.
    """
    caller = await add_member(
        db_session,
        hasher,
        ws,
        key="bound",
        email="reports-bound@example.com",
        template_name="Caller",
    )
    await login(api, caller)

    created = await api.post(
        ws.path("/dashboards"),
        headers=ws.owner.auth,
        json={
            "name": "Caller home",
            "shared": True,
            "template_id": str(ws.templates["Caller"].id),
            "layout": [{"widget": "follow_ups", "x": 0, "y": 0, "w": 4, "h": 2}],
        },
    )
    assert created.status_code == 201, created.text

    seen = await api.get(ws.path("/dashboards"), headers=caller.auth)
    assert seen.status_code == 200, seen.text
    assert "Caller home" in {row["name"] for row in seen.json()}


async def test_a_personal_dashboard_is_invisible_to_everyone_else(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    other = await add_member(
        db_session,
        hasher,
        ws,
        key="other",
        email="reports-other@example.com",
        template_name="Caller",
    )
    await login(api, other)

    created = await api.post(
        ws.path("/dashboards"),
        headers=ws.owner.auth,
        json={"name": "Just mine", "layout": []},
    )
    assert created.status_code == 201, created.text

    seen = await api.get(ws.path("/dashboards"), headers=other.auth)
    assert "Just mine" not in {row["name"] for row in seen.json()}

    direct = await api.get(ws.path(f"/dashboards/{created.json()['id']}"), headers=other.auth)
    # 404 rather than 403 — a 403 would confirm it exists.
    assert direct.status_code == 404


async def test_seeing_a_shared_dashboard_is_not_permission_to_rewrite_it(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    caller = await add_member(
        db_session,
        hasher,
        ws,
        key="reader",
        email="reports-reader@example.com",
        template_name="Caller",
    )
    await login(api, caller)

    created = await api.post(
        ws.path("/dashboards"),
        headers=ws.owner.auth,
        json={"name": "Team board", "shared": True, "layout": []},
    )
    response = await api.patch(
        ws.path(f"/dashboards/{created.json()['id']}"),
        headers=caller.auth,
        json={"name": "Mine now"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "not_your_dashboard"


async def test_choosing_a_landing_dashboard_does_not_change_anyone_else_s(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    first = await api.post(
        ws.path("/dashboards"), headers=ws.owner.auth, json={"name": "One", "layout": []}
    )
    second = await api.post(
        ws.path("/dashboards"), headers=ws.owner.auth, json={"name": "Two", "layout": []}
    )

    await api.put(ws.path(f"/dashboards/{first.json()['id']}/default"), headers=ws.owner.auth)
    await api.put(ws.path(f"/dashboards/{second.json()['id']}/default"), headers=ws.owner.auth)

    listed = {
        row["name"]: row
        for row in (await api.get(ws.path("/dashboards"), headers=ws.owner.auth)).json()
    }
    assert listed["Two"]["is_default"] is True
    assert listed["One"]["is_default"] is False


# --- ranges ------------------------------------------------------------------


async def test_an_inverted_or_enormous_range_is_refused(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    inverted = await api.get(
        ws.path("/reports/funnel"),
        headers=ws.owner.auth,
        params={"from": "2026-08-31", "to": "2026-08-01"},
    )
    assert inverted.status_code == 422
    assert inverted.json()["detail"]["code"] == "invalid_range"

    enormous = await api.get(
        ws.path("/reports/funnel"),
        headers=ws.owner.auth,
        params={"from": "2020-01-01", "to": "2026-01-01"},
    )
    assert enormous.status_code == 422
    assert enormous.json()["detail"]["code"] == "range_too_wide"
