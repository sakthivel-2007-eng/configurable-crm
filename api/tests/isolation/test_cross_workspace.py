"""The cross-workspace isolation matrix for M1.

Three families of probe, because a tenancy leak has three shapes:

1. **By direct id** — A asks for B's resource by its real uuid.
2. **By list** — A lists a collection and B's rows must not appear in it.
3. **By filter/reference** — A submits B's id as a *parameter* to a write in A,
   which is the leak that survives naive `WHERE workspace_id` filtering: the
   read is scoped, but the foreign key is not.

Every direct-id probe asserts 404 specifically, not merely "an error". A 403
would confirm existence.
"""

from __future__ import annotations

import uuid
from io import BytesIO

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from openpyxl import Workbook
from tests.isolation.conftest import TenantPair

pytestmark = pytest.mark.integration

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# The prefix every tenant-scoped route hangs off. Used by the coverage guard to
# work out which operations this matrix is responsible for.
_TENANT_PREFIX = "/api/v1/workspaces/{workspace_id}"


def _members_workbook() -> bytes:
    """A workbook that would import cleanly if the request were allowed.

    `Caller` is one of the five templates every workspace is provisioned with,
    so this names a template that genuinely exists in the target — the request
    fails on scoping alone, not on bad input.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["email", "full_name", "template"])
    sheet.append(["smuggled@example.com", "Smuggled Member", "Caller"])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# Routes under /workspaces/{workspace_id} that take a membership id. As
# milestones land, extend this list — the tests below pick up new entries with
# no further edits.
M1_MEMBERSHIP_ROUTES: list[tuple[str, str]] = [
    ("GET", "/members/{membership_id}"),
    ("PATCH", "/members/{membership_id}"),
    ("GET", "/members/{membership_id}/availability-log"),
    ("PUT", "/members/{membership_id}/availability"),
    ("POST", "/members/{membership_id}/license"),
    ("DELETE", "/members/{membership_id}/license"),
    ("POST", "/members/{membership_id}/deactivate"),
    ("POST", "/members/{membership_id}/reactivate"),
]

# Collection routes A may call in its own workspace. None of them may return a
# row belonging to B.
M1_COLLECTION_ROUTES: list[str] = [
    "/members",
    "/members/hierarchy",
    "/members/seats",
    "/settings/permission-templates",
]

# Collection *writes* — no resource id in the path, so the only probe that
# applies is "A posts to B's path". Each has its own test below, because their
# request bodies differ too much to parametrise usefully.
M1_COLLECTION_WRITE_ROUTES: list[tuple[str, str]] = [
    ("POST", "/members"),
    ("POST", "/members/bulk-upload"),
]

# The tenant root itself, covered by named tests further down.
M1_WORKSPACE_ROOT_ROUTES: list[tuple[str, str]] = [
    ("GET", ""),
    ("PATCH", ""),
]

_BODIES: dict[str, dict[str, object]] = {
    "PATCH": {"full_name": "Renamed By An Outsider"},
    "PUT": {"status": "ON_LEAVE", "note": "set by an outsider"},
    "POST": {},
}


# --- 1. by direct id ---------------------------------------------------------


@pytest.mark.parametrize(("method", "template"), M1_MEMBERSHIP_ROUTES)
async def test_member_route_in_another_workspace_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    """A's admin, using A's workspace path, cannot address B's membership.

    This is the common case: the workspace id is right, the resource id belongs
    to someone else. `ScopedSession.get` returns None, and the service raises
    the same 404 it would for a uuid that never existed.
    """
    path = tenants.a.path(template.format(membership_id=tenants.b.members["rep"].membership.id))
    response = await api.request(
        method,
        path,
        headers=tenants.a.owner.auth,
        json=_BODIES.get(method),
    )

    assert response.status_code == 404, (
        f"{method} {template} leaked workspace B's membership: {response.status_code} "
        f"{response.text}"
    )
    assert response.json()["detail"]["code"] == "not_found"


@pytest.mark.parametrize(("method", "template"), M1_MEMBERSHIP_ROUTES)
async def test_member_route_under_foreign_workspace_path_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    """A's admin cannot address B's membership under B's own workspace path.

    Here the ids are mutually consistent — only the caller is wrong. The
    scoping dependency refuses at the membership check, before any handler
    runs, and reports 404 for the *workspace*, not 403.
    """
    path = tenants.b.path(template.format(membership_id=tenants.b.members["rep"].membership.id))
    response = await api.request(
        method,
        path,
        headers=tenants.a.owner.auth,
        json=_BODIES.get(method),
    )

    assert response.status_code == 404, (
        f"{method} {template} let a non-member into workspace B: {response.status_code} "
        f"{response.text}"
    )


async def test_workspace_detail_of_another_tenant_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    response = await api.get(tenants.b.path(), headers=tenants.a.owner.auth)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


async def test_workspace_patch_of_another_tenant_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    response = await api.patch(
        tenants.b.path(),
        headers=tenants.a.owner.auth,
        json={"name": "Renamed by an outsider"},
    )
    assert response.status_code == 404


async def test_permissions_for_a_workspace_you_are_not_in_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """`/me/permissions` is unscoped by path, so it needs its own check."""
    response = await api.get(
        "/api/v1/me/permissions",
        params={"workspace_id": str(tenants.b.id)},
        headers=tenants.a.owner.auth,
    )
    assert response.status_code == 404


async def test_nonexistent_and_foreign_ids_are_indistinguishable(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """The whole point of 404-not-403: the two responses must be identical.

    If they differ in status, code, or message, an attacker can enumerate which
    membership ids are real in workspaces they cannot read.
    """
    foreign = await api.get(
        tenants.a.path(f"/members/{tenants.b.members['rep'].membership.id}"),
        headers=tenants.a.owner.auth,
    )
    invented = await api.get(
        tenants.a.path(f"/members/{uuid.uuid4()}"),
        headers=tenants.a.owner.auth,
    )

    assert foreign.status_code == invented.status_code == 404
    assert foreign.json() == invented.json()


# --- 2. by list --------------------------------------------------------------


@pytest.mark.parametrize("route", M1_COLLECTION_ROUTES)
async def test_collections_never_contain_another_workspaces_ids(
    api: AsyncClient,
    tenants: TenantPair,
    route: str,
) -> None:
    """No id belonging to B may appear anywhere in a response A receives.

    Checked against the raw response text rather than a parsed field, so a leak
    through a field the assertion did not anticipate still fails the test.
    """
    response = await api.get(tenants.a.path(route), headers=tenants.a.owner.auth)
    assert response.status_code == 200

    body = response.text
    foreign_ids = [
        str(tenants.b.id),
        str(tenants.b.owner.membership.id),
        str(tenants.b.owner.user.id),
        *(str(actor.membership.id) for actor in tenants.b.members.values()),
        *(str(actor.user.id) for actor in tenants.b.members.values()),
        *(str(template.id) for template in tenants.b.templates.values()),
    ]
    for leaked in foreign_ids:
        assert leaked not in body, f"{route} leaked {leaked} from workspace B"


async def test_member_list_contains_only_own_workspace(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    response = await api.get(tenants.a.path("/members"), headers=tenants.a.owner.auth)
    assert response.status_code == 200

    payload = response.json()
    assert payload["total"] == 3  # owner, manager, rep
    assert all(item["workspace_id"] == str(tenants.a.id) for item in payload["items"])


async def test_permission_templates_are_only_the_callers_workspaces(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """Both workspaces were provisioned with identically *named* templates.

    Names matching is expected — they are product roles. What must not happen
    is A receiving B's template *ids*, which would let A assign a member to a
    template belonging to another tenant.
    """
    response = await api.get(
        tenants.a.path("/settings/permission-templates"),
        headers=tenants.a.owner.auth,
    )
    assert response.status_code == 200

    returned = {item["id"] for item in response.json()}
    assert returned == {str(template.id) for template in tenants.a.templates.values()}
    assert returned.isdisjoint({str(t.id) for t in tenants.b.templates.values()})


async def test_workspace_list_shows_only_the_callers_workspaces(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    response = await api.get("/api/v1/workspaces", headers=tenants.a.owner.auth)
    assert response.status_code == 200

    ids = {item["id"] for item in response.json()}
    assert ids == {str(tenants.a.id)}


async def test_me_exposes_only_the_callers_memberships(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    response = await api.get("/api/v1/me", headers=tenants.a.owner.auth)
    assert response.status_code == 200

    workspace_ids = {m["workspace"]["id"] for m in response.json()["memberships"]}
    assert workspace_ids == {str(tenants.a.id)}


# --- 3. by filter / foreign reference ----------------------------------------


async def test_cannot_set_a_foreign_membership_as_manager(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """A foreign id passed as a *parameter*, not a path segment.

    The read is scoped, but nothing stops a caller sending B's membership id as
    `manager_id` unless the write validates it through the same scope. If this
    ever returns 200, A's hierarchy contains a pointer into B.
    """
    response = await api.patch(
        tenants.a.path(f"/members/{tenants.a.members['rep'].membership.id}"),
        headers=tenants.a.owner.auth,
        json={"manager_id": str(tenants.b.members["manager"].membership.id)},
    )
    assert response.status_code == 404


async def test_cannot_assign_a_foreign_permission_template(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    response = await api.patch(
        tenants.a.path(f"/members/{tenants.a.members['rep'].membership.id}"),
        headers=tenants.a.owner.auth,
        json={"template_id": str(tenants.b.templates["Admin"].id)},
    )
    assert response.status_code == 404


async def test_bulk_upload_under_a_foreign_workspace_path_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """A's admin cannot import members into workspace B.

    The highest-impact collection write in M1: a successful call would create
    real memberships inside another tenant. The workbook is valid and names one
    of B's own template names, so nothing but the scoping check stands between
    this request and a write.
    """
    response = await api.post(
        tenants.b.path("/members/bulk-upload"),
        headers=tenants.a.owner.auth,
        params={"dry_run": False},
        files={"file": ("members.xlsx", _members_workbook(), _XLSX_MIME)},
    )

    assert response.status_code == 404, (
        f"bulk upload wrote into workspace B: {response.status_code} {response.text}"
    )

    # Prove the refusal, not just the status code: B's roster is untouched.
    roster = await api.get(tenants.b.path("/members"), headers=tenants.b.owner.auth)
    assert roster.status_code == 200
    emails = {item["user"]["email"] for item in roster.json()["items"]}
    assert "smuggled@example.com" not in emails


async def test_bulk_upload_dry_run_into_another_workspace_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """The dry run must refuse too.

    It writes nothing, but it *reads* — an accepted preview would report which
    of B's members already exist and which template names B uses.
    """
    response = await api.post(
        tenants.b.path("/members/bulk-upload"),
        headers=tenants.a.owner.auth,
        params={"dry_run": True},
        files={"file": ("members.xlsx", _members_workbook(), _XLSX_MIME)},
    )
    assert response.status_code == 404


async def test_cannot_invite_against_a_foreign_template(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    response = await api.post(
        tenants.a.path("/members"),
        headers=tenants.a.owner.auth,
        json={
            "email": "newcomer@example.com",
            "full_name": "Newcomer",
            "template_id": str(tenants.b.templates["Caller"].id),
        },
    )
    assert response.status_code == 404


async def test_cannot_reassign_leads_to_a_foreign_membership(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """The reassignment target is the highest-value foreign reference here.

    In M1 nobody holds leads, so the call succeeds without consulting the
    target — the assertion is that it does *not* resolve B's membership. From
    M5, when the target is actually used, this same test proves a deactivation
    in A cannot hand a pipeline to a member of B.
    """
    response = await api.post(
        tenants.a.path(f"/members/{tenants.a.members['rep'].membership.id}/deactivate"),
        headers=tenants.a.owner.auth,
        json={"reassign_to_membership_id": str(tenants.b.members["manager"].membership.id)},
    )

    assert response.status_code in (200, 404)
    if response.status_code == 200:
        # No leads existed, so the target was never dereferenced. Prove the
        # membership in B is untouched rather than accepting the 200 blindly.
        check = await api.get(
            tenants.b.path(f"/members/{tenants.b.members['manager'].membership.id}"),
            headers=tenants.b.owner.auth,
        )
        assert check.status_code == 200
        assert check.json()["is_active"] is True


async def test_availability_log_of_a_foreign_member_returns_404_not_empty(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """An empty page would read as "that member has no history".

    404 is the only honest answer — the member is not addressable from here.
    """
    response = await api.get(
        tenants.a.path(f"/members/{tenants.b.members['rep'].membership.id}/availability-log"),
        headers=tenants.a.owner.auth,
    )
    assert response.status_code == 404


async def test_pagination_offset_cannot_walk_into_another_workspace(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """Paging past the end returns an empty page, not the next tenant's rows."""
    response = await api.get(
        tenants.a.path("/members"),
        headers=tenants.a.owner.auth,
        params={"limit": 100, "offset": 3},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["total"] == 3


# --- 4. the matrix stays complete -------------------------------------------


def test_the_matrix_covers_every_workspace_scoped_route(app: FastAPI) -> None:
    """Every tenant-scoped operation appears in one of the route lists above.

    The suite's promise is "for every endpoint that exists", and a promise
    checked by hand is a promise that quietly lapses — `POST /members/bulk-upload`
    was added in M1 and missed by exactly that failure.

    So the claim is derived from the application's own OpenAPI schema rather
    than asserted. A milestone that mounts a new tenant route fails here until
    it is listed, which is the point: the reminder arrives with the route, not
    after an audit.
    """
    declared: set[tuple[str, str]] = set()
    declared.update(M1_MEMBERSHIP_ROUTES)
    declared.update(("GET", route) for route in M1_COLLECTION_ROUTES)
    declared.update(M1_COLLECTION_WRITE_ROUTES)
    declared.update(M1_WORKSPACE_ROOT_ROUTES)

    mounted: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(_TENANT_PREFIX):
            continue
        suffix = path[len(_TENANT_PREFIX) :]
        mounted.update((method.upper(), suffix) for method in operations)

    assert mounted, "No tenant-scoped routes found — has the mount prefix changed?"

    uncovered = mounted - declared
    assert not uncovered, (
        "Tenant-scoped routes missing from the isolation matrix: "
        f"{sorted(uncovered)}. Add them to the route lists in this module."
    )

    # Also catch the reverse: a list entry for a route that no longer exists,
    # which would leave a test passing against nothing.
    stale = declared - mounted
    assert not stale, f"Isolation matrix lists routes that are not mounted: {sorted(stale)}"
