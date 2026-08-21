"""Cross-workspace isolation for reports and dashboards (M9).

Every endpoint here is a lead read wearing an aggregate's clothing, which makes
the leak subtler than usual: nobody gets another workspace's *records*, they get
its *totals* — and a total is very often all somebody wanted. "How many leads
does that competitor have" is answered as well by a funnel as by a list.

A dashboard id is a smaller prize but a real one: a shared dashboard's layout
names the fields somebody thought worth watching.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from tests.isolation.conftest import TenantPair
from tests.isolation.test_cross_workspace import (
    _M9_BODIES,
    M9_COLLECTION_ROUTES,
    M9_ID_ROUTES,
)

pytestmark = pytest.mark.integration


async def _dashboard_in_b(api: AsyncClient, tenants: TenantPair) -> str:
    response = await api.post(
        tenants.b.path("/dashboards"),
        headers=tenants.b.owner.auth,
        json={
            "name": "B's board",
            "shared": True,
            "layout": [{"widget": "funnel", "x": 0, "y": 0, "w": 6, "h": 4}],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _leads_in_b(api: AsyncClient, tenants: TenantPair, count: int) -> None:
    for index in range(count):
        response = await api.post(
            tenants.b.path("/leads"),
            headers=tenants.b.owner.auth,
            json={"values": {"name": f"B lead {index}", "phone": f"+1999556{index:04d}"}},
        )
        assert response.status_code == 201, response.text


@pytest.mark.parametrize(("method", "template"), M9_ID_ROUTES)
async def test_a_dashboard_id_from_another_workspace_is_a_404(
    api: AsyncClient, tenants: TenantPair, method: str, template: str
) -> None:
    dashboard_id = await _dashboard_in_b(api, tenants)
    path = tenants.a.path(template.format(dashboard_id=dashboard_id))
    body = _M9_BODIES.get(template)

    kwargs: dict[str, object] = {"headers": tenants.a.owner.auth}
    if body is not None and method in ("PATCH", "POST"):
        kwargs["json"] = body

    response = await api.request(method, path, **kwargs)  # type: ignore[arg-type]
    assert response.status_code == 404, f"{method} {template} leaked: {response.text}"


@pytest.mark.parametrize("route", M9_COLLECTION_ROUTES)
async def test_a_report_never_counts_another_workspace_s_leads(
    api: AsyncClient, tenants: TenantPair, route: str
) -> None:
    """The subtle leak: not records, totals.

    Every one of these is scoped, so A's numbers must be A's alone however many
    leads B has.
    """
    await _leads_in_b(api, tenants, 3)
    await _dashboard_in_b(api, tenants)

    response = await api.get(tenants.a.path(route), headers=tenants.a.owner.auth)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert "B's board" not in response.text
    if route in ("/dashboard/leads-by-stage", "/reports/funnel"):
        assert sum(row["count"] for row in payload) == 0, (
            f"{route} counted another workspace's leads"
        )
    if route == "/dashboard/follow-ups":
        assert payload["never_contacted"] == 0


async def test_breakdown_cannot_group_by_another_workspace_s_field(
    api: AsyncClient, tenants: TenantPair
) -> None:
    """A field key is guessable — a colleague's field is named the obvious thing.

    So a key that exists only in B reads as `unknown_field` in A, not as an
    empty chart that confirms it exists.
    """
    created = await api.post(
        tenants.b.path("/settings/lead-fields"),
        headers=tenants.b.owner.auth,
        json={"label": "Secret segment", "field_type": "TEXT"},
    )
    assert created.status_code == 201, created.text
    key = created.json()["key"]

    response = await api.get(
        tenants.a.path("/reports/breakdown"),
        headers=tenants.a.owner.auth,
        params={"field_key": key},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_field"


async def test_the_leaderboard_lists_only_this_workspace_s_members(
    api: AsyncClient, tenants: TenantPair
) -> None:
    """Otherwise it is an org chart of somebody else's company."""
    response = await api.get(tenants.a.path("/reports/leaderboard"), headers=tenants.a.owner.auth)
    assert response.status_code == 200, response.text
    names = {row["name"] for row in response.json()}
    assert tenants.b.owner.user.full_name not in names


async def test_a_dashboard_cannot_be_bound_to_another_workspace_s_template(
    api: AsyncClient, tenants: TenantPair
) -> None:
    """Otherwise a dashboard in A could be aimed at a role that is not A's."""
    templates = await api.get(
        tenants.b.path("/settings/permission-templates"), headers=tenants.b.owner.auth
    )
    foreign = str(templates.json()[0]["id"])

    response = await api.post(
        tenants.a.path("/dashboards"),
        headers=tenants.a.owner.auth,
        json={"name": "Aimed elsewhere", "shared": True, "template_id": foreign, "layout": []},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_template"


async def test_a_report_write_rejects_a_caller_from_another_workspace(
    api: AsyncClient, tenants: TenantPair
) -> None:
    response = await api.post(
        tenants.a.path("/dashboards"),
        headers=tenants.b.owner.auth,
        json={"name": "Smuggled", "layout": []},
    )
    assert response.status_code in (403, 404), response.text
