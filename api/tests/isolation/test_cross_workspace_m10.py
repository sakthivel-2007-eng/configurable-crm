"""Cross-workspace isolation for the integration surface (M10).

Every id in this milestone is a capability rather than a record:

- a **webhook id** lets you point another workspace's events at your own server
- an **outbox id** lets you replay their events
- an **API key id** lets you revoke their live integration

And the API key itself is the sharpest edge in the product — it is the one
credential that carries a workspace with it rather than taking one from the URL,
so a key from workspace B presented against workspace A's data must resolve to
B and only B, whatever the request says.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from tests.isolation.conftest import TenantPair
from tests.isolation.test_cross_workspace import (
    _M10_BODIES,
    M10_COLLECTION_ROUTES,
    M10_COLLECTION_WRITE_ROUTES,
    M10_ID_ROUTES,
)

pytestmark = pytest.mark.integration


async def _template_id(api: AsyncClient, tenants: TenantPair, which: str) -> str:
    fixture = tenants.b if which == "b" else tenants.a
    response = await api.get(
        fixture.path("/settings/permission-templates"), headers=fixture.owner.auth
    )
    assert response.status_code == 200, response.text
    return str(response.json()[0]["id"])


async def _key_in_b(api: AsyncClient, tenants: TenantPair) -> tuple[str, str]:
    template_id = await _template_id(api, tenants, "b")
    response = await api.post(
        tenants.b.path("/settings/api-keys"),
        headers=tenants.b.owner.auth,
        json={"name": "B's integration", "permission_template_id": template_id},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"]), str(response.json()["key"])


async def _webhook_in_b(api: AsyncClient, tenants: TenantPair) -> str:
    template_id = await _template_id(api, tenants, "b")
    response = await api.post(
        tenants.b.path("/settings/webhooks"),
        headers=tenants.b.owner.auth,
        json={
            "name": "B's consumer",
            "url": "https://b.example.com/hook",
            "permission_template_id": template_id,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.mark.parametrize(("method", "template"), M10_ID_ROUTES)
async def test_an_integration_id_from_another_workspace_is_a_404(
    api: AsyncClient, tenants: TenantPair, method: str, template: str
) -> None:
    key_id, _ = await _key_in_b(api, tenants)
    endpoint_id = await _webhook_in_b(api, tenants)
    path = tenants.a.path(
        template.format(
            key_id=key_id,
            endpoint_id=endpoint_id,
            event_id="00000000-0000-4000-8000-000000000009",
        )
    )
    body = _M10_BODIES.get(template)

    kwargs: dict[str, object] = {"headers": tenants.a.owner.auth}
    if body is not None:
        kwargs["json"] = body

    response = await api.request(method, path, **kwargs)  # type: ignore[arg-type]
    assert response.status_code == 404, f"{method} {template} leaked: {response.text}"


@pytest.mark.parametrize("route", M10_COLLECTION_ROUTES)
async def test_integration_collections_never_show_another_workspace_s_rows(
    api: AsyncClient, tenants: TenantPair, route: str
) -> None:
    await _key_in_b(api, tenants)
    await _webhook_in_b(api, tenants)

    response = await api.get(tenants.a.path(route), headers=tenants.a.owner.auth)
    assert response.status_code == 200, response.text
    body = response.text
    assert "B's integration" not in body
    assert "B's consumer" not in body
    assert "b.example.com" not in body


@pytest.mark.parametrize(("method", "route"), M10_COLLECTION_WRITE_ROUTES)
async def test_integration_writes_reject_a_caller_from_another_workspace(
    api: AsyncClient, tenants: TenantPair, method: str, route: str
) -> None:
    body = dict(_M10_BODIES.get(route, {}))
    body["permission_template_id"] = await _template_id(api, tenants, "b")

    response = await api.request(
        method, tenants.a.path(route), headers=tenants.b.owner.auth, json=body
    )
    assert response.status_code in (403, 404), (
        f"{method} {route} answered {response.status_code} to a foreign caller"
    )


async def test_a_key_writes_only_into_its_own_workspace(
    api: AsyncClient, tenants: TenantPair
) -> None:
    """The sharpest edge in the product.

    An API key carries its workspace rather than taking one from the URL, which
    is what makes `/intake/leads` unscoped. So the test is not "can B's key hit
    A's path" — there is no such path — it is that a lead posted with B's key
    lands in B and is invisible in A, whatever else the payload says.
    """
    _, secret = await _key_in_b(api, tenants)

    created = await api.post(
        "/api/v1/intake/leads",
        headers={"X-API-Key": secret},
        json={
            "identity": "+919000009999",
            "values": {"name": "Posted with B's key"},
            # A hostile payload naming A's workspace. It is not a parameter the
            # endpoint reads, and this asserts it stays that way.
            "workspace_id": str(tenants.a.workspace.id),
        },
    )
    assert created.status_code == 200, created.text
    lead_id = created.json()["lead_id"]

    in_b = await api.get(tenants.b.path(f"/leads/{lead_id}"), headers=tenants.b.owner.auth)
    assert in_b.status_code == 200, "the lead did not land in the key's own workspace"

    in_a = await api.get(tenants.a.path(f"/leads/{lead_id}"), headers=tenants.a.owner.auth)
    assert in_a.status_code == 404, "a key wrote into another workspace"


async def test_a_key_cannot_be_created_against_another_workspace_s_template(
    api: AsyncClient, tenants: TenantPair
) -> None:
    """Otherwise a key in A could borrow B's grants.

    The template lookup is scoped, so B's id simply is not a template here —
    which reads as 422 rather than confirming it exists elsewhere.
    """
    foreign_template = await _template_id(api, tenants, "b")
    response = await api.post(
        tenants.a.path("/settings/api-keys"),
        headers=tenants.a.owner.auth,
        json={"name": "Borrowed grants", "permission_template_id": foreign_template},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_template"


async def test_the_intake_log_is_scoped_to_the_key_s_workspace(
    api: AsyncClient, tenants: TenantPair
) -> None:
    _, secret = await _key_in_b(api, tenants)
    await api.post(
        "/api/v1/intake/leads",
        headers={"X-API-Key": secret},
        json={"identity": "+919000009998", "values": {"name": "B's traffic"}},
    )

    in_a = await api.get(tenants.a.path("/settings/intake-log"), headers=tenants.a.owner.auth)
    assert in_a.status_code == 200
    assert in_a.json()["total"] == 0, "A can see B's intake traffic"

    in_b = await api.get(tenants.b.path("/settings/intake-log"), headers=tenants.b.owner.auth)
    assert in_b.json()["total"] == 1
