"""Cross-workspace isolation for M4's permission templates.

The field matrix is the most sensitive configuration surface in the product: it
describes exactly which fields a competitor considers worth restricting, and
reading one would expose their whole schema alongside it.

Every workspace is provisioned with the same five template *names*, so the id is
the only thing distinguishing them — precisely the case a naive lookup by name
would get wrong.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from tests.isolation.conftest import TenantPair
from tests.isolation.test_cross_workspace import (
    _M4_BODIES,
    M4_COLLECTION_ROUTES,
    M4_COLLECTION_WRITE_ROUTES,
    M4_TEMPLATE_ROUTES,
    M4_TENANT_FREE_ROUTES,
)

pytestmark = pytest.mark.integration


async def _template_id(api: AsyncClient, tenants: TenantPair, which: str, name: str) -> str:
    fixture = tenants.b if which == "b" else tenants.a
    body = (
        await api.get(fixture.path("/settings/permission-templates"), headers=fixture.owner.auth)
    ).json()
    return str(next(t["id"] for t in body if t["name"] == name))


@pytest.mark.parametrize("route", M4_COLLECTION_ROUTES)
async def test_template_collections_never_contain_another_workspaces_ids(
    api: AsyncClient,
    tenants: TenantPair,
    route: str,
) -> None:
    foreign = await _template_id(api, tenants, "b", "Caller")
    response = await api.get(tenants.a.path(route), headers=tenants.a.owner.auth)

    assert response.status_code == 200, response.text
    if route in M4_TENANT_FREE_ROUTES:
        # A product constant: identical for every workspace, nothing to leak.
        return
    assert str(tenants.b.id) not in response.text
    assert foreign not in response.text


@pytest.mark.parametrize(("method", "template"), M4_TEMPLATE_ROUTES)
async def test_template_route_in_another_workspace_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    """A's admin cannot read or edit B's template by id."""
    foreign = await _template_id(api, tenants, "b", "Caller")
    response = await api.request(
        method,
        tenants.a.path(template.format(template_id=foreign)),
        headers=tenants.a.owner.auth,
        json=_M4_BODIES.get(template),
    )
    assert response.status_code == 404, (
        f"{method} {template} reached workspace B's template: {response.text}"
    )
    assert response.json()["detail"]["code"] == "not_found"


@pytest.mark.parametrize(("method", "template"), M4_TEMPLATE_ROUTES)
async def test_template_route_under_foreign_workspace_path_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    foreign = await _template_id(api, tenants, "b", "Caller")
    response = await api.request(
        method,
        tenants.b.path(template.format(template_id=foreign)),
        headers=tenants.a.owner.auth,
        json=_M4_BODIES.get(template),
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(("method", "route"), M4_COLLECTION_WRITE_ROUTES)
async def test_template_writes_under_a_foreign_workspace_path_return_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    route: str,
) -> None:
    response = await api.request(
        method,
        tenants.b.path(route),
        headers=tenants.a.owner.auth,
        json={"name": "Smuggled Template"},
    )
    assert response.status_code == 404, response.text


async def test_a_grant_cannot_name_a_field_from_another_workspace(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """The by-reference leak, on the surface where it matters most.

    A grant row naming B's field would put a foreign field id inside A's matrix,
    and the projection service resolves grants to field *keys* — so a collision
    on key would then quietly widen A's access.
    """
    caller = await _template_id(api, tenants, "a", "Caller")
    response = await api.put(
        tenants.a.path(f"/settings/permission-templates/{caller}/field-grants"),
        headers=tenants.a.owner.auth,
        json={
            "grants": [{"field_id": str(tenants.b.builtin_field_id), "view": True, "edit": True}]
        },
    )
    assert response.status_code == 404


async def test_a_bulk_grant_cannot_name_a_foreign_field(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    caller = await _template_id(api, tenants, "a", "Caller")
    response = await api.put(
        tenants.a.path(f"/settings/permission-templates/{caller}/field-grants/bulk"),
        headers=tenants.a.owner.auth,
        json={
            "grant": "VIEW",
            "value": True,
            "field_ids": [str(tenants.b.builtin_field_id)],
        },
    )
    assert response.status_code == 404


async def test_a_lead_view_cannot_name_a_foreign_field(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    caller = await _template_id(api, tenants, "a", "Caller")
    response = await api.put(
        tenants.a.path(f"/settings/permission-templates/{caller}/lead-view"),
        headers=tenants.a.owner.auth,
        json={"layout": [{"label": "X", "field_ids": [str(tenants.b.builtin_field_id)]}]},
    )
    assert response.status_code == 404


async def test_the_matrix_of_one_workspace_never_mentions_anothers_fields(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """Read side of the same concern: A's matrix lists A's fields only."""
    caller = await _template_id(api, tenants, "a", "Caller")
    matrix = await api.get(
        tenants.a.path(f"/settings/permission-templates/{caller}/field-grants"),
        headers=tenants.a.owner.auth,
    )

    assert matrix.status_code == 200
    assert str(tenants.b.builtin_field_id) not in matrix.text
    # The four built-ins and nothing else.
    assert matrix.json()["columns"]["view"]["total"] == 4
