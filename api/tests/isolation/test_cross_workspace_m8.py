"""Cross-workspace isolation for M8's routing configuration (M8).

Assignment rules are the highest-leverage configuration in the product: a rule
decides where every future lead lands. If one workspace could read or edit
another's rules by guessing a uuid, it could quietly redirect that business's
entire incoming pipeline — and the leads would look, on every report, as though
they had been assigned normally.

`POST /leads/distribute` is the matching write. It takes a list of lead ids, so
a foreign id smuggled into the list must not be reassigned alongside the
caller's own; and its `config` names memberships, so a foreign membership id
must not become an assignee either.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from tests.isolation.conftest import TenantPair
from tests.isolation.test_cross_workspace import (
    _M8_BODIES,
    M8_COLLECTION_ROUTES,
    M8_COLLECTION_WRITE_ROUTES,
    M8_GROUP_ROUTES,
    M8_RULE_ROUTES,
)

pytestmark = pytest.mark.integration


async def _group_in_b(api: AsyncClient, tenants: TenantPair) -> str:
    response = await api.post(
        tenants.b.path("/settings/sales-groups"),
        headers=tenants.b.owner.auth,
        json={"name": "B's team"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _rule_in_b(api: AsyncClient, tenants: TenantPair) -> str:
    response = await api.post(
        tenants.b.path("/settings/assignment-rules"),
        headers=tenants.b.owner.auth,
        json={"name": "B's routing", "strategy": "UNASSIGNED", "config": {}},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _lead_in_b(api: AsyncClient, tenants: TenantPair) -> str:
    response = await api.post(
        tenants.b.path("/leads"),
        headers=tenants.b.owner.auth,
        json={"values": {"name": "B's lead", "phone": "+19995550101"}},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.mark.parametrize(("method", "template"), M8_GROUP_ROUTES)
async def test_a_sales_group_id_from_another_workspace_is_a_404(
    api: AsyncClient, tenants: TenantPair, method: str, template: str
) -> None:
    group_id = await _group_in_b(api, tenants)
    path = tenants.a.path(template.format(group_id=group_id))
    body = _M8_BODIES.get(template)

    kwargs: dict[str, object] = {"headers": tenants.a.owner.auth}
    if method == "PUT":
        kwargs["json"] = []
    elif body is not None:
        kwargs["json"] = body

    response = await api.request(method, path, **kwargs)  # type: ignore[arg-type]
    assert response.status_code == 404, f"{method} {template} leaked: {response.text}"


@pytest.mark.parametrize(("method", "template"), M8_RULE_ROUTES)
async def test_an_assignment_rule_id_from_another_workspace_is_a_404(
    api: AsyncClient, tenants: TenantPair, method: str, template: str
) -> None:
    """The one that matters most: rules decide where future leads go."""
    rule_id = await _rule_in_b(api, tenants)
    path = tenants.a.path(template.format(rule_id=rule_id))
    body = _M8_BODIES.get(template)

    kwargs: dict[str, object] = {"headers": tenants.a.owner.auth}
    if body is not None:
        kwargs["json"] = body

    response = await api.request(method, path, **kwargs)  # type: ignore[arg-type]
    assert response.status_code == 404, f"{method} {template} leaked: {response.text}"

    # And B's rule is untouched.
    check = await api.get(
        tenants.b.path("/settings/assignment-rules"), headers=tenants.b.owner.auth
    )
    assert check.status_code == 200
    rules = {r["id"]: r for r in check.json()}
    assert rules[rule_id]["name"] == "B's routing"
    assert rules[rule_id]["is_active"] is True


@pytest.mark.parametrize("route", M8_COLLECTION_ROUTES)
async def test_routing_collections_never_show_another_workspace_s_rows(
    api: AsyncClient, tenants: TenantPair, route: str
) -> None:
    await _group_in_b(api, tenants)
    await _rule_in_b(api, tenants)

    response = await api.get(tenants.a.path(route), headers=tenants.a.owner.auth)
    assert response.status_code == 200, response.text
    names = {row["name"] for row in response.json()}
    assert "B's team" not in names
    assert "B's routing" not in names


@pytest.mark.parametrize(("method", "route"), M8_COLLECTION_WRITE_ROUTES)
async def test_routing_writes_reject_a_caller_from_another_workspace(
    api: AsyncClient, tenants: TenantPair, method: str, route: str
) -> None:
    """B's credentials against A's path: refused before anything is written."""
    body = _M8_BODIES.get(route, {})
    kwargs: dict[str, object] = {"headers": tenants.b.owner.auth}
    if method != "GET":
        kwargs["json"] = body

    response = await api.request(method, tenants.a.path(route), **kwargs)  # type: ignore[arg-type]
    assert response.status_code in (403, 404), (
        f"{method} {route} answered {response.status_code} to a foreign caller"
    )


async def test_distribute_cannot_reassign_a_lead_from_another_workspace(
    api: AsyncClient, tenants: TenantPair
) -> None:
    """A foreign lead id in the list is not distributed alongside the caller's."""
    foreign = await _lead_in_b(api, tenants)

    own = await api.post(
        tenants.a.path("/leads"),
        headers=tenants.a.owner.auth,
        json={"values": {"name": "A's lead", "phone": "+19995550202"}},
    )
    assert own.status_code == 201, own.text

    response = await api.post(
        tenants.a.path("/leads/distribute"),
        headers=tenants.a.owner.auth,
        json={
            "lead_ids": [str(own.json()["id"]), foreign],
            "strategy": "FIXED",
            "config": {"membership_id": str(tenants.a.owner.membership.id)},
        },
    )
    assert response.status_code == 200, response.text
    # The foreign id was invisible to the scoped query, so only one lead was
    # considered at all.
    assert response.json()["total"] == 1

    check = await api.get(tenants.b.path(f"/leads/{foreign}"), headers=tenants.b.owner.auth)
    assert check.status_code == 200
    assert check.json()["assignee_id"] is None, "another workspace reassigned B's lead"


async def test_a_rule_cannot_target_a_membership_from_another_workspace(
    api: AsyncClient, tenants: TenantPair
) -> None:
    """A foreign membership id must not become an assignee.

    The rule would look valid in A's settings and quietly route A's leads to
    somebody who cannot see them — a pipeline that vanishes rather than errors.
    """
    foreign_member = str(tenants.b.owner.membership.id)

    created = await api.post(
        tenants.a.path("/settings/assignment-rules"),
        headers=tenants.a.owner.auth,
        json={
            "name": "Points at B",
            "strategy": "FIXED",
            "config": {"membership_id": foreign_member},
        },
    )
    assert created.status_code == 201, created.text

    lead = await api.post(
        tenants.a.path("/leads"),
        headers=tenants.a.owner.auth,
        json={"values": {"name": "Routed", "phone": "+19995550303"}},
    )
    assert lead.status_code == 201, lead.text
    # The engine's eligibility check is a scoped read, so B's membership is not
    # a member here and the lead stays unassigned rather than leaving the tenant.
    assert lead.json()["assignee_id"] is None
