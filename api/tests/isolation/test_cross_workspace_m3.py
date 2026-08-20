"""Cross-workspace isolation for M3's pipeline and taxonomy.

Split into its own module rather than appended to the M1/M2 matrix, because
these probes need a helper that fetches a *real* id from the other workspace
first — every workspace provisions the same shapes, so a leak here is easy to
miss by eye and trivial to catch by id.

The route lists themselves live in `test_cross_workspace`, where the coverage
guard reads them.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from tests.isolation.conftest import TenantPair
from tests.isolation.test_cross_workspace import (
    _M3_BODIES,
    M3_ACTION_ROUTES,
    M3_COLLECTION_ROUTES,
    M3_COLLECTION_WRITE_ROUTES,
    M3_ID_ROUTES,
)

pytestmark = pytest.mark.integration


async def _foreign_taxonomy_id(api: AsyncClient, tenants: TenantPair, id_kind: str) -> str:
    """One of workspace B's provisioned taxonomy ids, by kind."""
    if id_kind == "stage":
        body = (
            await api.get(tenants.b.path("/settings/stages"), headers=tenants.b.owner.auth)
        ).json()
        return str(body["active"][0]["id"])
    route = {
        "lost_reason": "/settings/lost-reasons",
        "disposition": "/settings/call-dispositions",
    }[id_kind]
    body = (await api.get(tenants.b.path(route), headers=tenants.b.owner.auth)).json()
    return str(body[0]["id"])


@pytest.mark.parametrize("route", M3_COLLECTION_ROUTES)
async def test_taxonomy_collections_never_contain_another_workspaces_ids(
    api: AsyncClient,
    tenants: TenantPair,
    route: str,
) -> None:
    response = await api.get(tenants.a.path(route), headers=tenants.a.owner.auth)
    assert response.status_code == 200, response.text
    assert str(tenants.b.id) not in response.text


@pytest.mark.parametrize(("method", "template", "id_kind"), M3_ID_ROUTES)
async def test_taxonomy_route_in_another_workspace_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
    id_kind: str,
) -> None:
    """A's admin cannot reach B's stage, lost reason or disposition by id."""
    foreign_id = await _foreign_taxonomy_id(api, tenants, id_kind)
    path = tenants.a.path(
        template.format(stage_id=foreign_id, reason_id=foreign_id, disposition_id=foreign_id)
    )
    response = await api.request(
        method, path, headers=tenants.a.owner.auth, json=_M3_BODIES.get(template)
    )
    assert response.status_code == 404, (
        f"{method} {template} reached workspace B's taxonomy: {response.text}"
    )
    assert response.json()["detail"]["code"] == "not_found"


@pytest.mark.parametrize(("method", "route"), M3_COLLECTION_WRITE_ROUTES)
async def test_taxonomy_writes_under_a_foreign_workspace_path_return_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    route: str,
) -> None:
    """A's admin cannot add a stage, reason or disposition inside workspace B."""
    bodies: dict[str, dict[str, object]] = {
        "/settings/stages": {"label": "Smuggled"},
        "/settings/stages/reorder": {"ordered_ids": []},
        "/settings/lost-reasons": {"label": "Smuggled"},
        "/settings/call-dispositions": {"label": "Smuggled"},
        "/settings/preferences": {"currency": "EUR"},
        "/settings/custom-actions": {"name": "Smuggled"},
    }
    response = await api.request(
        method, tenants.b.path(route), headers=tenants.a.owner.auth, json=bodies[route]
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(("method", "template"), M3_ACTION_ROUTES)
async def test_custom_action_route_in_another_workspace_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    """Custom actions are the deepest configurability in the product, so a
    cross-tenant read here would expose the most about a competitor."""
    created = await api.post(
        tenants.b.path("/settings/custom-actions"),
        headers=tenants.b.owner.auth,
        json={"name": "Theirs Only", "score": 25},
    )
    assert created.status_code == 201, created.text
    foreign = created.json()

    path = tenants.a.path(
        template.format(type_id=foreign["id"], field_id=foreign["fields"][0]["id"])
    )
    response = await api.request(
        method, path, headers=tenants.a.owner.auth, json=_M3_BODIES.get(template)
    )
    assert response.status_code == 404, (
        f"{method} {template} reached workspace B's custom action: {response.text}"
    )


async def test_a_disabled_feature_is_still_404_not_403_across_a_boundary(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """Ordering matters: the scoping check must run *before* the feature check.

    If the feature gate answered first, a non-member would learn from a 403
    whether workspace B has the module enabled — a small leak, but a real one,
    and exactly the kind that accumulates into a profile of another tenant.
    """
    disabled = await api.patch(
        tenants.b.path("/settings/preferences"),
        headers=tenants.b.owner.auth,
        json={"features": {"custom_actions": False}},
    )
    assert disabled.status_code == 200

    response = await api.get(
        tenants.b.path("/settings/custom-actions"), headers=tenants.a.owner.auth
    )
    assert response.status_code == 404, (
        "a non-member learned this workspace's feature configuration"
    )
