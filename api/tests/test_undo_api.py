"""Bulk edit, the edit report, and undo (M7).

M7's definition of done, verbatim:

> Bulk-edit 300 leads, undo it fully, and show the conflict path when one of
> them was edited in between.

All three are here. The conflict path gets the most attention because it is the
one the handoff singles out — "a lead edited after the changeset is a conflict.
Report it and let the operator decide. Never silently clobber a later edit" —
and because getting it wrong is invisible until it destroys someone's work.
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
async def workspace(db_session: AsyncSession, hasher: PasswordHasherService) -> WorkspaceFixture:
    fixture = await build_workspace(
        db_session, hasher, name="Undo", owner_email="undo-owner@example.com"
    )
    await add_member(
        db_session,
        hasher,
        fixture,
        key="rep",
        email="undo-rep@example.com",
        template_name="Caller",
    )
    await db_session.commit()
    return fixture


async def _admin(api: AsyncClient, workspace: WorkspaceFixture) -> dict[str, str]:
    await login(api, workspace.owner)
    return workspace.owner.auth


async def _field(
    api: AsyncClient, ws: WorkspaceFixture, headers: dict[str, str], **payload: Any
) -> dict:
    response = await api.post(ws.path("/settings/lead-fields"), headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _lead(
    api: AsyncClient, ws: WorkspaceFixture, headers: dict[str, str], **values: Any
) -> dict:
    response = await api.post(ws.path("/leads"), headers=headers, json={"values": values})
    assert response.status_code == 201, response.text
    return response.json()


async def _get_lead(
    api: AsyncClient, ws: WorkspaceFixture, headers: dict[str, str], lead_id: str
) -> dict:
    response = await api.get(ws.path(f"/leads/{lead_id}"), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _bulk(
    api: AsyncClient, ws: WorkspaceFixture, headers: dict[str, str], **body: Any
) -> dict:
    response = await api.post(ws.path("/leads/bulk"), headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def _grant_bulk_edit(
    api: AsyncClient, ws: WorkspaceFixture, headers: dict[str, str], template_name: str
) -> None:
    """Give a template the capability to bulk edit, and nothing else.

    The two checks are deliberately layered — "may this caller use this endpoint
    at all" before "which fields may they touch" — so a test about the *field*
    check has to get past the capability one first. Granting it here exercises
    the real permission surface rather than assuming what a provisioned template
    happens to allow.
    """
    listed = (await api.get(ws.path("/settings/permission-templates"), headers=headers)).json()
    summary = next(t for t in listed if t["name"] == template_name)

    # The list is a summary; capabilities come with the detail.
    detail = (
        await api.get(ws.path(f"/settings/permission-templates/{summary['id']}"), headers=headers)
    ).json()

    capabilities = dict(detail["capabilities"])
    capabilities["leads"] = {**capabilities.get("leads", {}), "bulk_edit": True}

    response = await api.patch(
        ws.path(f"/settings/permission-templates/{summary['id']}"),
        headers=headers,
        json={"capabilities": capabilities},
    )
    assert response.status_code == 200, response.text


# --- bulk edit ---------------------------------------------------------------


async def test_a_bulk_edit_opens_exactly_one_changeset(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Rule 5a, at the scale that makes it matter.

    A changeset per lead would turn "undo that mistake" into 300 separate
    decisions, which is the same as having no undo.
    """
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")
    ids = [
        (await _lead(api, workspace, headers, name=f"L{i}", phone=f"96{i:08d}"))["id"]
        for i in range(5)
    ]

    before = (await api.get(workspace.path("/changesets"), headers=headers)).json()["total"]
    result = await _bulk(api, workspace, headers, lead_ids=ids, values={"city": "Chennai"})
    after = (await api.get(workspace.path("/changesets"), headers=headers)).json()["total"]

    assert result["leads_updated"] == 5
    assert after == before + 1, "one run, one changeset"

    detail = (
        await api.get(workspace.path(f"/changesets/{result['changeset_id']}"), headers=headers)
    ).json()
    assert detail["lead_count"] == 5
    assert len({a["lead_id"] for a in detail["actions"]}) == 5


async def test_the_bulk_summary_names_what_changed(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The edit report is unreadable without it, and it cannot be reconstructed
    afterwards from the actions alone."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")
    ids = [
        (await _lead(api, workspace, headers, name=f"S{i}", phone=f"97{i:08d}"))["id"]
        for i in range(3)
    ]

    result = await _bulk(api, workspace, headers, lead_ids=ids, values={"city": "Delhi"})
    assert result["summary"] == "Set City on 3 leads"


async def test_a_bulk_edit_over_the_cap_is_refused_not_truncated(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Truncating would report success over work it did not do."""
    headers = await _admin(api, workspace)
    response = await api.post(
        workspace.path("/leads/bulk"),
        headers=headers,
        json={
            "lead_ids": [f"00000000-0000-4000-8000-{i:012d}" for i in range(501)],
            "values": {"name": "x"},
        },
    )
    # Pydantic's max_length rejects it before the service does; either way the
    # request is refused rather than silently trimmed.
    assert response.status_code == 422, response.text


async def test_a_bulk_edit_refuses_a_field_the_caller_cannot_edit(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Rule 4 at batch scale: rejected by name, never silently dropped."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Salary", field_type="NUMBER")
    lead = await _lead(api, workspace, headers, name="Earner", phone="9800000001")

    await _grant_bulk_edit(api, workspace, headers, "Caller")

    rep = workspace.members["rep"]
    await login(api, rep)
    response = await api.post(
        workspace.path("/leads/bulk"),
        headers=rep.auth,
        json={"lead_ids": [lead["id"]], "values": {"salary": 1}},
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "field_not_editable"


async def test_a_bulk_edit_naming_an_unavailable_lead_is_refused(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """ "297 of 300 updated" teaches the operator something. "300" misleads them."""
    headers = await _admin(api, workspace)
    real = await _lead(api, workspace, headers, name="Real", phone="9810000001")

    response = await api.post(
        workspace.path("/leads/bulk"),
        headers=headers,
        json={
            "lead_ids": [real["id"], "00000000-0000-4000-8000-000000000001"],
            "values": {"name": "Renamed"},
        },
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "leads_not_found"


# --- the acceptance check ----------------------------------------------------


async def test_three_hundred_leads_can_be_bulk_edited_and_fully_undone(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """M7's definition of done, at the size it names."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Owner Note", field_type="TEXT")

    ids: list[str] = []
    for index in range(300):
        created = await _lead(
            api,
            workspace,
            headers,
            name=f"Bulk {index}",
            phone=f"9820{index:06d}",
            owner_note="before",
        )
        ids.append(created["id"])

    result = await _bulk(api, workspace, headers, lead_ids=ids, values={"owner_note": "after"})
    assert result["leads_updated"] == 300
    changeset_id = result["changeset_id"]

    sampled = await _get_lead(api, workspace, headers, ids[0])
    assert sampled["values"]["owner_note"] == "after"

    preview = await api.post(
        workspace.path(f"/changesets/{changeset_id}/preview-undo"), headers=headers
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["counts"] == {
        "total": 300,
        "reversible": 300,
        "conflicted": 0,
        "deleted": 0,
    }

    undone = await api.post(
        workspace.path(f"/changesets/{changeset_id}/undo"), headers=headers, json={}
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["leads_reverted"] == 300
    assert undone.json()["leads_skipped"] == 0

    for lead_id in (ids[0], ids[150], ids[299]):
        reverted = await _get_lead(api, workspace, headers, lead_id)
        assert reverted["values"]["owner_note"] == "before"


async def test_a_lead_edited_after_the_batch_is_a_conflict(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The rule the handoff singles out.

    The later edit is somebody's work. Undo reports it and stops; it does not
    decide on their behalf.
    """
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Owner Note", field_type="TEXT")
    ids = [
        (
            await _lead(
                api, workspace, headers, name=f"C{i}", phone=f"9830{i:06d}", owner_note="before"
            )
        )["id"]
        for i in range(3)
    ]

    result = await _bulk(api, workspace, headers, lead_ids=ids, values={"owner_note": "after"})
    changeset_id = result["changeset_id"]

    # Somebody edits one of them afterwards.
    meddled = await api.patch(
        workspace.path(f"/leads/{ids[1]}"),
        headers=headers,
        json={"values": {"owner_note": "someone else's work"}},
    )
    assert meddled.status_code == 200, meddled.text

    preview = (
        await api.post(workspace.path(f"/changesets/{changeset_id}/preview-undo"), headers=headers)
    ).json()
    assert preview["counts"]["conflicted"] == 1
    assert preview["counts"]["reversible"] == 2

    conflicted = next(p for p in preview["leads"] if p["outcome"] == "CONFLICTED")
    reversal = conflicted["reversals"][0]
    # The operator is told exactly what diverged, not merely that something did.
    assert reversal["expected"] == "after"
    assert reversal["current"] == "someone else's work"
    assert reversal["revert_to"] == "before"

    refused = await api.post(
        workspace.path(f"/changesets/{changeset_id}/undo"), headers=headers, json={}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "undo_conflicts"

    # Nothing was touched by the refusal.
    untouched = await _get_lead(api, workspace, headers, ids[0])
    assert untouched["values"]["owner_note"] == "after"

    proceeded = await api.post(
        workspace.path(f"/changesets/{changeset_id}/undo"),
        headers=headers,
        json={"skip_conflicts": True},
    )
    assert proceeded.status_code == 200, proceeded.text
    assert proceeded.json()["leads_reverted"] == 2
    assert proceeded.json()["leads_skipped"] == 1

    assert (await _get_lead(api, workspace, headers, ids[0]))["values"]["owner_note"] == "before"
    # The later edit survived, which is the entire point.
    assert (await _get_lead(api, workspace, headers, ids[1]))["values"][
        "owner_note"
    ] == "someone else's work"


# --- undo semantics ----------------------------------------------------------


async def test_undo_reverses_stage_assignee_and_rating(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    rep = workspace.members["rep"]
    stages = (await api.get(workspace.path("/settings/stages"), headers=headers)).json()
    lead = await _lead(api, workspace, headers, name="Multi", phone="9840000001")

    result = await _bulk(
        api,
        workspace,
        headers,
        lead_ids=[lead["id"]],
        stage_id=stages["active"][0]["id"],
        assignee_id=str(rep.membership.id),
        rating=4,
    )

    changed = await _get_lead(api, workspace, headers, lead["id"])
    assert changed["stage_id"] == stages["active"][0]["id"]
    assert changed["assignee_id"] == str(rep.membership.id)
    assert changed["rating"] == 4

    undone = await api.post(
        workspace.path(f"/changesets/{result['changeset_id']}/undo"), headers=headers, json={}
    )
    assert undone.status_code == 200, undone.text

    back = await _get_lead(api, workspace, headers, lead["id"])
    assert back["stage_id"] == stages["initial"]["id"]
    assert back["assignee_id"] is None
    assert back["rating"] is None


async def test_a_note_is_not_reversible(api: AsyncClient, workspace: WorkspaceFixture) -> None:
    """Undoing an edit must not claim a conversation did not happen."""
    headers = await _admin(api, workspace)
    lead = await _lead(api, workspace, headers, name="Noted", phone="9850000001")

    noted = await api.post(
        workspace.path(f"/leads/{lead['id']}/notes"),
        headers=headers,
        json={"body": "They called back"},
    )
    assert noted.status_code == 201, noted.text
    changeset_id = noted.json()["changeset_id"]

    response = await api.post(
        workspace.path(f"/changesets/{changeset_id}/undo"), headers=headers, json={}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "nothing_to_undo"

    timeline = (
        await api.get(workspace.path(f"/leads/{lead['id']}/actions"), headers=headers)
    ).json()
    assert any(a["kind"] == "NOTE" for a in timeline["items"])


async def test_an_undo_is_itself_a_changeset_that_can_be_undone(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """`undo_of_id` is what marks an undo, so undoing one is not a special case."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")
    lead = await _lead(api, workspace, headers, name="Round", phone="9860000001", city="Chennai")

    first = await _bulk(api, workspace, headers, lead_ids=[lead["id"]], values={"city": "Delhi"})
    undone = (
        await api.post(
            workspace.path(f"/changesets/{first['changeset_id']}/undo"), headers=headers, json={}
        )
    ).json()
    assert (await _get_lead(api, workspace, headers, lead["id"]))["values"]["city"] == "Chennai"

    undo_changeset = (
        await api.get(workspace.path(f"/changesets/{undone['undo_changeset_id']}"), headers=headers)
    ).json()
    assert undo_changeset["undo_of_id"] == first["changeset_id"]

    # Undo the undo: back to Delhi.
    again = await api.post(
        workspace.path(f"/changesets/{undone['undo_changeset_id']}/undo"),
        headers=headers,
        json={},
    )
    assert again.status_code == 200, again.text
    assert (await _get_lead(api, workspace, headers, lead["id"]))["values"]["city"] == "Delhi"


async def test_undoing_twice_is_refused(api: AsyncClient, workspace: WorkspaceFixture) -> None:
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")
    lead = await _lead(api, workspace, headers, name="Once", phone="9870000001", city="A")

    result = await _bulk(api, workspace, headers, lead_ids=[lead["id"]], values={"city": "B"})
    path = workspace.path(f"/changesets/{result['changeset_id']}/undo")

    assert (await api.post(path, headers=headers, json={})).status_code == 200
    repeat = await api.post(path, headers=headers, json={})
    assert repeat.status_code == 409
    assert repeat.json()["detail"]["code"] == "already_undone"


async def test_a_field_changed_and_changed_back_is_not_a_conflict(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Conflict detection compares values, not timestamps.

    Somebody who edited a lead and then reverted it by hand has left it exactly
    as the batch set it, so the undo is still safe — a "was this touched since"
    check would refuse here for no reason.
    """
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")
    lead = await _lead(api, workspace, headers, name="Wobble", phone="9880000001", city="A")

    result = await _bulk(api, workspace, headers, lead_ids=[lead["id"]], values={"city": "B"})
    for value in ("C", "B"):
        await api.patch(
            workspace.path(f"/leads/{lead['id']}"),
            headers=headers,
            json={"values": {"city": value}},
        )

    preview = (
        await api.post(
            workspace.path(f"/changesets/{result['changeset_id']}/preview-undo"), headers=headers
        )
    ).json()
    assert preview["counts"]["conflicted"] == 0

    undone = await api.post(
        workspace.path(f"/changesets/{result['changeset_id']}/undo"), headers=headers, json={}
    )
    assert undone.status_code == 200, undone.text
    assert (await _get_lead(api, workspace, headers, lead["id"]))["values"]["city"] == "A"


async def test_undo_restores_the_search_vector(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """A vector left describing the undone values would make the lead findable
    by text it no longer contains — which reads as search being broken."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Company", field_type="TEXT")
    lead = await _lead(
        api, workspace, headers, name="Searchable", phone="9890000001", company="Oldname"
    )

    result = await _bulk(
        api, workspace, headers, lead_ids=[lead["id"]], values={"company": "Newname"}
    )
    await api.post(
        workspace.path(f"/changesets/{result['changeset_id']}/undo"), headers=headers, json={}
    )

    found = (
        await api.get(workspace.path("/leads"), headers=headers, params={"q": "Oldname"})
    ).json()
    assert {item["id"] for item in found["items"]} == {lead["id"]}
    stale = (
        await api.get(workspace.path("/leads"), headers=headers, params={"q": "Newname"})
    ).json()
    assert stale["total"] == 0


async def test_undo_refuses_a_field_the_caller_cannot_edit(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Replaying values the workspace already accepted is still a write, and the
    caller reverting them may not be the one who set them."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Salary", field_type="NUMBER")
    lead = await _lead(api, workspace, headers, name="Paid", phone="9900000001", salary=10)

    result = await _bulk(api, workspace, headers, lead_ids=[lead["id"]], values={"salary": 20})
    await _grant_bulk_edit(api, workspace, headers, "Caller")

    rep = workspace.members["rep"]
    await login(api, rep)
    response = await api.post(
        workspace.path(f"/changesets/{result['changeset_id']}/undo"),
        headers=rep.auth,
        json={},
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "field_not_editable"


# --- the edit report ---------------------------------------------------------


async def test_the_edit_report_filters_by_source_and_actor(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """ "What did the 9am import do" and "what has Priya changed today" are the
    two questions anyone brings to this screen."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")
    lead = await _lead(api, workspace, headers, name="Reported", phone="9910000001")
    await _bulk(api, workspace, headers, lead_ids=[lead["id"]], values={"city": "Chennai"})

    bulk_only = (
        await api.get(
            workspace.path("/changesets"), headers=headers, params={"source": "BULK_EDIT"}
        )
    ).json()
    assert bulk_only["total"] == 1
    assert all(c["source"] == "BULK_EDIT" for c in bulk_only["items"])

    by_actor = (
        await api.get(
            workspace.path("/changesets"),
            headers=headers,
            params={"actor_id": str(workspace.owner.membership.id)},
        )
    ).json()
    assert by_actor["total"] >= 2

    stranger = (
        await api.get(
            workspace.path("/changesets"),
            headers=headers,
            params={"actor_id": str(workspace.members["rep"].membership.id)},
        )
    ).json()
    assert stranger["total"] == 0


async def test_the_edit_report_can_show_only_undone_batches(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")
    lead = await _lead(api, workspace, headers, name="Flagged", phone="9920000001", city="A")

    result = await _bulk(api, workspace, headers, lead_ids=[lead["id"]], values={"city": "B"})
    await api.post(
        workspace.path(f"/changesets/{result['changeset_id']}/undo"), headers=headers, json={}
    )

    undone = (
        await api.get(workspace.path("/changesets"), headers=headers, params={"undone": "true"})
    ).json()
    assert [c["id"] for c in undone["items"]] == [result["changeset_id"]]
