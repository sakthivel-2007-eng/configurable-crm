"""Cross-workspace isolation for M5's leads, actions and templates.

This is the surface that actually holds a customer's business: their contacts,
what was said to them, and when. Everything before it was configuration; a leak
here is the incident.

The probes use *real* leads created inside workspace B, because a fabricated
uuid would 404 for the boring reason and prove nothing.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from tests.isolation.conftest import TenantPair
from tests.isolation.test_cross_workspace import (
    _M5_BODIES,
    M5_COLLECTION_ROUTES,
    M5_COLLECTION_WRITE_ROUTES,
    M5_LEAD_ROUTES,
)

pytestmark = pytest.mark.integration


async def _lead_in(api: AsyncClient, tenants: TenantPair, which: str, phone: str) -> dict:
    fixture = tenants.b if which == "b" else tenants.a
    response = await api.post(
        fixture.path("/leads"),
        headers=fixture.owner.auth,
        json={"values": {"name": f"Lead in {which}", "phone": phone}},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(("method", "template"), M5_LEAD_ROUTES)
async def test_lead_route_in_another_workspace_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    """A's admin cannot touch B's lead by id, under A's own workspace path."""
    foreign = await _lead_in(api, tenants, "b", "9100000001")
    response = await api.request(
        method,
        tenants.a.path(template.format(lead_id=foreign["id"])),
        headers=tenants.a.owner.auth,
        json=_M5_BODIES.get(template),
    )
    assert response.status_code == 404, (
        f"{method} {template} reached workspace B's lead: {response.text}"
    )
    assert response.json()["detail"]["code"] == "not_found"


@pytest.mark.parametrize(("method", "template"), M5_LEAD_ROUTES)
async def test_lead_route_under_foreign_workspace_path_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    """Consistent ids, wrong caller — refused before any handler runs."""
    foreign = await _lead_in(api, tenants, "b", "9100000002")
    response = await api.request(
        method,
        tenants.b.path(template.format(lead_id=foreign["id"])),
        headers=tenants.a.owner.auth,
        json=_M5_BODIES.get(template),
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("route", M5_COLLECTION_ROUTES)
async def test_lead_collections_never_contain_another_workspaces_rows(
    api: AsyncClient,
    tenants: TenantPair,
    route: str,
) -> None:
    """Checked against the raw response text so a leak through an unanticipated
    field still fails."""
    foreign = await _lead_in(api, tenants, "b", "9100000003")
    await _lead_in(api, tenants, "a", "9100000004")

    response = await api.get(tenants.a.path(route), headers=tenants.a.owner.auth)
    assert response.status_code == 200, response.text
    assert foreign["id"] not in response.text
    assert str(tenants.b.id) not in response.text


@pytest.mark.parametrize(("method", "route"), M5_COLLECTION_WRITE_ROUTES)
async def test_lead_writes_under_a_foreign_workspace_path_return_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    route: str,
) -> None:
    bodies: dict[str, dict[str, object]] = {
        "/leads": {"values": {"name": "Smuggled", "phone": "9100000005"}},
        "/templates": {"channel": "SMS", "name": "Smuggled", "body": "hi"},
    }
    response = await api.request(
        method, tenants.b.path(route), headers=tenants.a.owner.auth, json=bodies[route]
    )
    assert response.status_code == 404, response.text

    # And nothing was written into B.
    roster = await api.get(tenants.b.path(route), headers=tenants.b.owner.auth)
    assert "Smuggled" not in roster.text


async def test_the_same_identity_can_exist_in_both_workspaces(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """Dedupe is per workspace, not global.

    Two customers may genuinely hold the same person's number, and a global
    unique constraint would leak the fact that someone else already has them.
    """
    first = await _lead_in(api, tenants, "a", "9100000010")
    second = await _lead_in(api, tenants, "b", "9100000010")

    assert first["identity_value"] == second["identity_value"]
    assert first["id"] != second["id"]


async def test_a_lead_cannot_be_assigned_to_a_foreign_member(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """The by-reference leak: the read is scoped but the foreign key might not
    be. Assigning A's lead to B's rep would put a member of one tenant into
    another's pipeline."""
    response = await api.post(
        tenants.a.path("/leads"),
        headers=tenants.a.owner.auth,
        json={
            "values": {"name": "Cross", "phone": "9100000011"},
            "assignee_id": str(tenants.b.members["rep"].membership.id),
        },
    )
    assert response.status_code == 404


async def test_a_lead_cannot_be_moved_to_a_foreign_stage(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    own = await _lead_in(api, tenants, "a", "9100000012")
    foreign_stages = (
        await api.get(tenants.b.path("/settings/stages"), headers=tenants.b.owner.auth)
    ).json()

    response = await api.patch(
        tenants.a.path(f"/leads/{own['id']}"),
        headers=tenants.a.owner.auth,
        json={"stage_id": foreign_stages["active"][0]["id"]},
    )
    assert response.status_code == 404


async def test_a_call_cannot_use_a_foreign_disposition(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    own = await _lead_in(api, tenants, "a", "9100000013")
    foreign_dispositions = (
        await api.get(tenants.b.path("/settings/call-dispositions"), headers=tenants.b.owner.auth)
    ).json()

    response = await api.post(
        tenants.a.path(f"/leads/{own['id']}/calls"),
        headers=tenants.a.owner.auth,
        json={
            "direction": "OUTGOING",
            "disposition_id": foreign_dispositions[0]["id"],
            "duration_seconds": 30,
        },
    )
    assert response.status_code == 404


async def test_a_custom_action_cannot_use_a_foreign_action_type(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    own = await _lead_in(api, tenants, "a", "9100000014")
    foreign_type = (
        await api.post(
            tenants.b.path("/settings/custom-actions"),
            headers=tenants.b.owner.auth,
            json={"name": "Theirs", "score": 10},
        )
    ).json()

    response = await api.post(
        tenants.a.path(f"/leads/{own['id']}/custom-actions"),
        headers=tenants.a.owner.auth,
        json={"action_type_id": foreign_type["id"], "values": {"notes": "x"}},
    )
    assert response.status_code == 404


async def test_a_timeline_never_shows_another_workspaces_actions(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """The actions table is the audit trail; reading another tenant's would
    expose who they called and what was said."""
    foreign = await _lead_in(api, tenants, "b", "9100000015")
    await api.post(
        tenants.b.path(f"/leads/{foreign['id']}/notes"),
        headers=tenants.b.owner.auth,
        json={"body": "CONFIDENTIAL: they are ready to sign"},
    )

    own = await _lead_in(api, tenants, "a", "9100000016")
    timeline = await api.get(
        tenants.a.path(f"/leads/{own['id']}/actions"), headers=tenants.a.owner.auth
    )
    assert timeline.status_code == 200
    assert "CONFIDENTIAL" not in timeline.text


async def test_the_edit_report_never_shows_another_workspaces_changesets(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    foreign = await _lead_in(api, tenants, "b", "9100000017")
    await api.patch(
        tenants.b.path(f"/leads/{foreign['id']}"),
        headers=tenants.b.owner.auth,
        json={"values": {"name": "Their Private Rename"}},
    )

    report = await api.get(tenants.a.path("/changesets"), headers=tenants.a.owner.auth)
    assert report.status_code == 200
    assert "Their Private Rename" not in report.text
    assert foreign["identity_value"] not in report.text


async def test_a_template_cannot_be_rendered_against_a_foreign_lead(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """Rendering reads a lead's values — the most direct way to exfiltrate one
    if the lead lookup were not scoped."""
    foreign = await _lead_in(api, tenants, "b", "9100000018")
    template = await api.post(
        tenants.a.path("/templates"),
        headers=tenants.a.owner.auth,
        json={"channel": "SMS", "name": "Probe", "body": "Name is {{name}}"},
    )
    assert template.status_code == 201

    response = await api.post(
        tenants.a.path(f"/templates/{template.json()['id']}/render"),
        headers=tenants.a.owner.auth,
        json={"lead_id": foreign["id"]},
    )
    assert response.status_code == 404
    assert "Lead in b" not in response.text
