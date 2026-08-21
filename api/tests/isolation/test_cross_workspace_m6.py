"""Cross-workspace isolation for M6's filters, layouts and filtered search.

A saved filter is a stored *query*, which makes it a leak with two surfaces
rather than one: the filter row itself, and whatever running it returns. Both
are probed here.

The third surface is subtler and gets its own tests at the bottom. A filter
document names field keys and stage ids, and a compiler that resolved those
against the wrong workspace would turn `POST /leads/search` into a way to ask
questions about B's schema from inside A. Since the search endpoint takes ids
in a *body* rather than a path, the scoping dependency alone does not cover it.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from tests.isolation.conftest import TenantPair
from tests.isolation.test_cross_workspace import (
    _M6_BODIES,
    M6_COLLECTION_ROUTES,
    M6_COLLECTION_WRITE_ROUTES,
    M6_FILTER_ROUTES,
)

pytestmark = pytest.mark.integration

_EMPTY = {"type": "group", "op": "AND", "children": []}


async def _filter_in(api: AsyncClient, tenants: TenantPair, which: str, name: str) -> dict:
    fixture = tenants.b if which == "b" else tenants.a
    response = await api.post(
        fixture.path("/filters"),
        headers=fixture.owner.auth,
        json={"name": name, "definition": _EMPTY, "visibility": "SHARED"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _lead_in(api: AsyncClient, tenants: TenantPair, which: str, phone: str) -> dict:
    fixture = tenants.b if which == "b" else tenants.a
    response = await api.post(
        fixture.path("/leads"),
        headers=fixture.owner.auth,
        json={"values": {"name": f"Lead in {which}", "phone": phone}},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- 1. by direct id ---------------------------------------------------------


@pytest.mark.parametrize(("method", "template"), M6_FILTER_ROUTES)
async def test_filter_route_in_another_workspace_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    """A real filter in B, addressed by A under A's own path."""
    foreign = await _filter_in(api, tenants, "b", "B's filter")
    response = await api.request(
        method,
        tenants.a.path(template.format(filter_id=foreign["id"])),
        headers=tenants.a.owner.auth,
        json=_M6_BODIES.get(template),
    )
    assert response.status_code == 404, (
        f"{method} {template} reached workspace B's filter: {response.text}"
    )
    assert response.json()["detail"]["code"] == "not_found"


@pytest.mark.parametrize(("method", "template"), M6_FILTER_ROUTES)
async def test_filter_route_under_foreign_workspace_path_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    foreign = await _filter_in(api, tenants, "b", "B's other filter")
    response = await api.request(
        method,
        tenants.b.path(template.format(filter_id=foreign["id"])),
        headers=tenants.a.owner.auth,
        json=_M6_BODIES.get(template),
    )
    assert response.status_code == 404, response.text


# --- 2. by list --------------------------------------------------------------


@pytest.mark.parametrize("route", M6_COLLECTION_ROUTES)
async def test_view_collections_never_contain_another_workspaces_rows(
    api: AsyncClient,
    tenants: TenantPair,
    route: str,
) -> None:
    foreign = await _filter_in(api, tenants, "b", "B only")
    await _filter_in(api, tenants, "a", "A only")

    response = await api.get(tenants.a.path(route), headers=tenants.a.owner.auth)
    assert response.status_code == 200, response.text
    assert foreign["id"] not in response.text
    assert str(tenants.b.id) not in response.text


@pytest.mark.parametrize(("method", "route"), M6_COLLECTION_WRITE_ROUTES)
async def test_view_writes_under_a_foreign_workspace_path_return_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    route: str,
) -> None:
    response = await api.request(
        method,
        tenants.b.path(route),
        headers=tenants.a.owner.auth,
        json=_M6_BODIES.get(route),
    )
    assert response.status_code == 404, f"{method} {route}: {response.text}"


async def test_search_never_returns_another_workspaces_leads(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """The endpoint that exists to return many leads at once.

    An empty filter matches everything, which makes this the widest possible
    probe: if scoping were missing, B's whole pipeline would come back.
    """
    foreign = await _lead_in(api, tenants, "b", "9200000001")
    await _lead_in(api, tenants, "a", "9200000002")

    response = await api.post(
        tenants.a.path("/leads/search"),
        headers=tenants.a.owner.auth,
        json={"filter": _EMPTY, "limit": 100},
    )
    assert response.status_code == 200, response.text
    assert foreign["id"] not in response.text
    assert response.json()["total"] == 1


# --- 3. by foreign reference in a filter document ----------------------------


async def test_a_filter_cannot_name_another_workspaces_field(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """Field keys are per-workspace, so a key only B has must not resolve in A.

    Refused as `unknown_field` — the same answer A gets for a key nobody has,
    because distinguishing them would confirm B's schema.
    """
    created = await api.post(
        tenants.b.path("/settings/lead-fields"),
        headers=tenants.b.owner.auth,
        json={"label": "Secret Budget", "field_type": "NUMBER"},
    )
    assert created.status_code == 201, created.text
    foreign_key = created.json()["key"]

    response = await api.post(
        tenants.a.path("/leads/search"),
        headers=tenants.a.owner.auth,
        json={"filter": {"type": "field", "key": foreign_key, "op": "gt", "value": 1}},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "unknown_field"


async def test_a_history_predicate_naming_a_foreign_stage_matches_nothing(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """Stage ids are uuids, so a foreign one is syntactically valid.

    It must simply never match: the subquery is correlated on the lead's own
    workspace, so B's stage id can only ever select zero of A's leads. The
    request succeeds — refusing it would confirm the id names something real.
    """
    await _lead_in(api, tenants, "a", "9200000003")
    stages = await api.get(tenants.b.path("/settings/stages"), headers=tenants.b.owner.auth)
    foreign_stage_id = stages.json()["won"]["id"]

    response = await api.post(
        tenants.a.path("/leads/search"),
        headers=tenants.a.owner.auth,
        json={
            "filter": {
                "type": "status_changed",
                "to_stage_id": foreign_stage_id,
                "within": {"last_days": 30},
            }
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0


async def test_a_role_filter_cannot_name_another_workspaces_template(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    templates = await api.get(
        tenants.b.path("/settings/permission-templates"), headers=tenants.b.owner.auth
    )
    foreign_template_id = templates.json()[0]["id"]

    response = await api.post(
        tenants.a.path("/filters"),
        headers=tenants.a.owner.auth,
        json={
            "name": "Smuggled role filter",
            "definition": _EMPTY,
            "visibility": "ROLE",
            "template_id": foreign_template_id,
        },
    )
    assert response.status_code == 404, response.text


async def test_a_layout_cannot_be_saved_against_another_workspaces_filter(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    foreign = await _filter_in(api, tenants, "b", "B's layout target")
    response = await api.put(
        tenants.a.path("/layouts"),
        headers=tenants.a.owner.auth,
        params={"filter_id": foreign["id"]},
        json={"columns": ["identity_value"]},
    )
    assert response.status_code == 404, response.text


async def test_a_fabricated_filter_id_is_indistinguishable_from_a_foreign_one(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """Both 404 with the same code, so absence and inaccessibility look alike."""
    foreign = await _filter_in(api, tenants, "b", "B's filter again")
    invented = uuid.uuid4()

    for filter_id in (foreign["id"], str(invented)):
        response = await api.get(
            tenants.a.path(f"/filters/{filter_id}"), headers=tenants.a.owner.auth
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "not_found"
