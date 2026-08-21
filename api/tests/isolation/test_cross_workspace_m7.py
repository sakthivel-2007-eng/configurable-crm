"""Cross-workspace isolation for M7's bulk edit and undo.

Undo is the most dangerous endpoint in the product to get wrong here: it
*writes* to whichever leads a changeset names, and it takes only the changeset's
id. If the id were resolved without a tenant filter, one workspace could rewrite
another's leads by guessing a uuid — and the write would look, in the audit
trail, exactly like a legitimate correction.

Bulk edit has the mirror-image risk: it takes a list of lead ids, so a foreign
id smuggled into the list must not be edited alongside the caller's own.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from tests.isolation.conftest import TenantPair
from tests.isolation.test_cross_workspace import (
    _M7_BODIES,
    M7_CHANGESET_ROUTES,
    M7_COLLECTION_ROUTES,
    M7_COLLECTION_WRITE_ROUTES,
    M7_EXPORT_ROUTES,
    M7_JOB_ROUTES,
    M7_LABEL_ROUTES,
    M7_TASK_ROUTES,
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


async def _changeset_in(api: AsyncClient, tenants: TenantPair, which: str, phone: str) -> str:
    """A real changeset in one workspace, produced by a real edit."""
    fixture = tenants.b if which == "b" else tenants.a
    lead = await _lead_in(api, tenants, which, phone)
    edited = await api.patch(
        fixture.path(f"/leads/{lead['id']}"),
        headers=fixture.owner.auth,
        json={"values": {"name": "Edited"}},
    )
    assert edited.status_code == 200, edited.text

    report = await api.get(fixture.path("/changesets"), headers=fixture.owner.auth)
    assert report.status_code == 200, report.text
    return str(report.json()["items"][0]["id"])


@pytest.mark.parametrize(("method", "template"), M7_CHANGESET_ROUTES)
async def test_changeset_route_in_another_workspace_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    """A's admin cannot read or reverse B's changeset, under A's own path."""
    foreign = await _changeset_in(api, tenants, "b", "9950000001")
    response = await api.request(
        method,
        tenants.a.path(template.format(changeset_id=foreign)),
        headers=tenants.a.owner.auth,
        json=_M7_BODIES.get(template),
    )
    assert response.status_code == 404, (
        f"{method} {template} reached workspace B's changeset: {response.text}"
    )
    assert response.json()["detail"]["code"] == "not_found"


@pytest.mark.parametrize(("method", "template"), M7_CHANGESET_ROUTES)
async def test_changeset_route_under_foreign_workspace_path_returns_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    template: str,
) -> None:
    foreign = await _changeset_in(api, tenants, "b", "9950000002")
    response = await api.request(
        method,
        tenants.b.path(template.format(changeset_id=foreign)),
        headers=tenants.a.owner.auth,
        json=_M7_BODIES.get(template),
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(("method", "route"), M7_COLLECTION_WRITE_ROUTES)
async def test_bulk_writes_under_a_foreign_workspace_path_return_404(
    api: AsyncClient,
    tenants: TenantPair,
    method: str,
    route: str,
) -> None:
    response = await api.request(
        method,
        tenants.b.path(route),
        headers=tenants.a.owner.auth,
        json=_M7_BODIES.get(route),
    )
    assert response.status_code == 404, f"{method} {route}: {response.text}"


async def test_a_bulk_edit_cannot_smuggle_a_foreign_lead_into_its_id_list(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """The leak a naive `WHERE id IN (...)` would let through.

    The read is scoped, so B's lead is simply absent — and because the service
    refuses a batch it cannot fully resolve rather than editing around the gap,
    A's own lead is left alone too. Silently editing one of the two would tell
    A that the other id belongs to someone.
    """
    mine = await _lead_in(api, tenants, "a", "9960000001")
    foreign = await _lead_in(api, tenants, "b", "9960000002")

    response = await api.post(
        tenants.a.path("/leads/bulk"),
        headers=tenants.a.owner.auth,
        json={"lead_ids": [mine["id"], foreign["id"]], "values": {"name": "Smuggled"}},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "leads_not_found"

    # B's lead is untouched.
    check = await api.get(tenants.b.path(f"/leads/{foreign['id']}"), headers=tenants.b.owner.auth)
    assert check.json()["values"]["name"] == "Lead in b"


async def test_the_edit_report_never_lists_another_workspaces_changesets(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    foreign = await _changeset_in(api, tenants, "b", "9970000001")
    await _changeset_in(api, tenants, "a", "9970000002")

    response = await api.get(tenants.a.path("/changesets"), headers=tenants.a.owner.auth)
    assert response.status_code == 200, response.text
    assert foreign not in response.text
    assert str(tenants.b.id) not in response.text


async def test_filtering_the_edit_report_by_a_foreign_actor_returns_nothing(
    api: AsyncClient,
    tenants: TenantPair,
) -> None:
    """A membership id is a uuid, so a foreign one is syntactically valid.

    It must match nothing rather than being refused — refusing would confirm
    the id names a real member somewhere.
    """
    await _changeset_in(api, tenants, "a", "9980000001")

    response = await api.get(
        tenants.a.path("/changesets"),
        headers=tenants.a.owner.auth,
        params={"actor_id": str(tenants.b.owner.membership.id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0


# --- tasks, labels and import jobs -------------------------------------------


async def _task_in(api: AsyncClient, tenants: TenantPair, which: str, title: str) -> dict:
    fixture = tenants.b if which == "b" else tenants.a
    response = await api.post(
        fixture.path("/tasks"),
        headers=fixture.owner.auth,
        json={"title": title, "due_at": "2026-09-01T09:00:00Z"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _label_in(api: AsyncClient, tenants: TenantPair, which: str, name: str) -> dict:
    fixture = tenants.b if which == "b" else tenants.a
    response = await api.post(
        fixture.path("/labels"), headers=fixture.owner.auth, json={"name": name}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _job_in(api: AsyncClient, tenants: TenantPair, which: str) -> dict:
    fixture = tenants.b if which == "b" else tenants.a
    response = await api.post(
        fixture.path("/imports"),
        headers=fixture.owner.auth,
        files={"file": ("leads.csv", b"Phone\n9000000001\n", "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(("method", "template"), M7_TASK_ROUTES)
async def test_task_route_in_another_workspace_returns_404(
    api: AsyncClient, tenants: TenantPair, method: str, template: str
) -> None:
    foreign = await _task_in(api, tenants, "b", "B's task")
    response = await api.request(
        method,
        tenants.a.path(template.format(task_id=foreign["id"])),
        headers=tenants.a.owner.auth,
        json=_M7_BODIES.get(template),
    )
    assert response.status_code == 404, f"{method} {template}: {response.text}"


@pytest.mark.parametrize(("method", "template"), M7_LABEL_ROUTES)
async def test_label_route_in_another_workspace_returns_404(
    api: AsyncClient, tenants: TenantPair, method: str, template: str
) -> None:
    foreign = await _label_in(api, tenants, "b", "B only")
    response = await api.request(
        method,
        tenants.a.path(template.format(label_id=foreign["id"])),
        headers=tenants.a.owner.auth,
        json=_M7_BODIES.get(template),
    )
    assert response.status_code == 404, f"{method} {template}: {response.text}"


@pytest.mark.parametrize(("method", "template"), M7_JOB_ROUTES)
async def test_import_job_route_in_another_workspace_returns_404(
    api: AsyncClient, tenants: TenantPair, method: str, template: str
) -> None:
    """An import job names an uploaded file and a mapping.

    Reaching one across the boundary would let A commit B's spreadsheet into
    B's leads, or simply read what columns B's customer data has.
    """
    foreign = await _job_in(api, tenants, "b")
    response = await api.request(
        method,
        tenants.a.path(template.format(job_id=foreign["id"])),
        headers=tenants.a.owner.auth,
        json=_M7_BODIES.get(template),
    )
    assert response.status_code == 404, f"{method} {template}: {response.text}"


@pytest.mark.parametrize("route", M7_COLLECTION_ROUTES)
async def test_work_collections_never_contain_another_workspaces_rows(
    api: AsyncClient, tenants: TenantPair, route: str
) -> None:
    foreign_task = await _task_in(api, tenants, "b", "B's task")
    foreign_label = await _label_in(api, tenants, "b", "B's label")
    foreign_job = await _job_in(api, tenants, "b")
    await _task_in(api, tenants, "a", "A's task")

    response = await api.get(tenants.a.path(route), headers=tenants.a.owner.auth)
    assert response.status_code == 200, response.text
    for foreign in (foreign_task["id"], foreign_label["id"], foreign_job["id"]):
        assert foreign not in response.text
    assert str(tenants.b.id) not in response.text


async def test_a_label_cannot_be_attached_across_the_boundary(
    api: AsyncClient, tenants: TenantPair
) -> None:
    """The two-id shape: a lead in A and a label in B.

    Both ids have to resolve inside the caller's workspace, and a check that
    only scoped one of them would let A tag its own lead with B's label —
    leaking B's taxonomy through the response.
    """
    mine = await _lead_in(api, tenants, "a", "9990000001")
    foreign_label = await _label_in(api, tenants, "b", "B's private tag")

    response = await api.post(
        tenants.a.path(f"/leads/{mine['id']}/labels/{foreign_label['id']}"),
        headers=tenants.a.owner.auth,
    )
    assert response.status_code == 404, response.text

    labels = await api.get(
        tenants.a.path(f"/leads/{mine['id']}/labels"), headers=tenants.a.owner.auth
    )
    assert labels.json() == []


async def test_a_task_cannot_be_created_against_a_foreign_lead(
    api: AsyncClient, tenants: TenantPair
) -> None:
    foreign = await _lead_in(api, tenants, "b", "9990000002")
    response = await api.post(
        tenants.a.path("/tasks"),
        headers=tenants.a.owner.auth,
        json={
            "title": "Smuggled",
            "due_at": "2026-09-01T09:00:00Z",
            "lead_id": foreign["id"],
        },
    )
    assert response.status_code == 404, response.text


async def test_a_task_cannot_be_assigned_to_a_foreign_member(
    api: AsyncClient, tenants: TenantPair
) -> None:
    response = await api.post(
        tenants.a.path("/tasks"),
        headers=tenants.a.owner.auth,
        json={
            "title": "Smuggled",
            "due_at": "2026-09-01T09:00:00Z",
            "assignee_id": str(tenants.b.owner.membership.id),
        },
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(("method", "template"), M7_EXPORT_ROUTES)
async def test_export_download_in_another_workspace_returns_404(
    api: AsyncClient, tenants: TenantPair, method: str, template: str
) -> None:
    """An export file *is* one workspace's customer data, in one object.

    Of everything in M7 this is the leak that would hand over the most at once,
    so the id is resolved through the scope like any other row.
    """
    await _lead_in(api, tenants, "b", "9991000001")
    started = await api.post(tenants.b.path("/leads/export"), headers=tenants.b.owner.auth, json={})
    assert started.status_code == 200, started.text
    foreign_job = started.json()["job_id"]

    response = await api.request(
        method,
        tenants.a.path(template.format(job_id=foreign_job)),
        headers=tenants.a.owner.auth,
    )
    assert response.status_code == 404, response.text


async def test_a_merge_cannot_pull_in_a_foreign_lead(api: AsyncClient, tenants: TenantPair) -> None:
    """Merging moves a lead's whole timeline onto the primary.

    Across the boundary that would copy B's calls and notes into A and then
    soft-delete B's record — a leak and a destruction in one call.
    """
    mine = await _lead_in(api, tenants, "a", "9992000001")
    foreign = await _lead_in(api, tenants, "b", "9992000002")

    response = await api.post(
        tenants.a.path("/leads/merge"),
        headers=tenants.a.owner.auth,
        json={"primary_id": mine["id"], "merge_ids": [foreign["id"]]},
    )
    assert response.status_code == 404, response.text

    # B's lead is untouched and still readable by B.
    check = await api.get(tenants.b.path(f"/leads/{foreign['id']}"), headers=tenants.b.owner.auth)
    assert check.status_code == 200


async def test_duplicates_never_mention_another_workspaces_leads(
    api: AsyncClient, tenants: TenantPair
) -> None:
    """Two workspaces can hold the same phone number legitimately.

    Grouping across the boundary would tell A that B has a customer in common —
    which is exactly the kind of inference tenancy exists to prevent.
    """
    shared_number = "9993000001"
    await _lead_in(api, tenants, "a", shared_number)
    foreign = await _lead_in(api, tenants, "b", shared_number)

    response = await api.get(tenants.a.path("/leads/duplicates"), headers=tenants.a.owner.auth)
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert foreign["id"] not in response.text
