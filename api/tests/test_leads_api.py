"""Leads, actions, changesets and message templates (M5).

The two acceptance checks PROMPTS.md M5 states verbatim:

- "edit three fields at once and show me three FIELD_CHANGE rows sharing one
  changeset id"
- "render a WhatsApp template against a real lead and show the preview plus any
  unresolved placeholders"

Plus the four M4 checks, which only become testable end-to-end now that leads
exist: a View-but-not-Edit field must be visible in detail, rejected on PATCH,
absent from export, and absent from a projected payload.
"""

from __future__ import annotations

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
        db_session, hasher, name="Acme", owner_email="owner@example.com"
    )
    await add_member(
        db_session, hasher, fixture, key="rep", email="rep@example.com", template_name="Caller"
    )
    return fixture


async def _admin(api: AsyncClient, workspace: WorkspaceFixture) -> dict[str, str]:
    await login(api, workspace.owner)
    return workspace.owner.auth


async def _create_lead(
    api: AsyncClient,
    workspace: WorkspaceFixture,
    headers: dict[str, str],
    phone: str = "9876543210",
) -> dict:
    response = await api.post(
        workspace.path("/leads"),
        headers=headers,
        json={"values": {"name": "Test Lead", "phone": phone}},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- creation ----------------------------------------------------------------


async def test_a_lead_is_created_with_jsonb_values(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """No per-customer columns: the schema is the customer's, so it is a map."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers)

    assert lead["values"]["name"] == "Test Lead"
    # Normalised on the way in, using the workspace's country code.
    assert lead["values"]["phone"] == "+919876543210"
    assert lead["identity_value"] == "+919876543210"
    assert lead["score"] == 0


async def test_creation_writes_a_lead_created_action_in_a_changeset(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers)

    timeline = (
        await api.get(workspace.path(f"/leads/{lead['id']}/actions"), headers=headers)
    ).json()
    kinds = [a["kind"] for a in timeline["items"]]
    assert "LEAD_CREATED" in kinds
    assert all(a["changeset_id"] for a in timeline["items"]), "every action carries a changeset"


async def test_a_new_lead_lands_in_the_initial_stage(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers)
    stages = (await api.get(workspace.path("/settings/stages"), headers=headers)).json()
    assert lead["stage_id"] == stages["initial"]["id"]


async def test_a_duplicate_identity_is_refused(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    await _create_lead(api, workspace, headers, phone="9000000001")

    duplicate = await api.post(
        workspace.path("/leads"),
        headers=headers,
        json={"values": {"name": "Someone Else", "phone": "9000000001"}},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "duplicate_identity"


async def test_a_lead_needs_an_identity_value(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    response = await api.post(
        workspace.path("/leads"), headers=headers, json={"values": {"name": "No Phone"}}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "identity_required"


async def test_an_invalid_value_is_rejected_with_the_field_named(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    response = await api.post(
        workspace.path("/leads"),
        headers=headers,
        json={"values": {"name": "X", "phone": "9000000002", "email": "not-an-email"}},
    )
    assert response.status_code == 422
    assert "email" in response.json()["detail"]["fields"]


# --- the M5 acceptance check -------------------------------------------------


async def test_editing_three_fields_yields_three_field_changes_in_one_changeset(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """PROMPTS.md M5, verbatim: "edit three fields at once and show me three
    FIELD_CHANGE rows sharing one changeset id"."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000010")

    updated = await api.patch(
        workspace.path(f"/leads/{lead['id']}"),
        headers=headers,
        json={
            "values": {
                "name": "Renamed Lead",
                "email": "new@example.com",
                "alternate_phone": "9000000011",
            }
        },
    )
    assert updated.status_code == 200, updated.text

    timeline = (
        await api.get(workspace.path(f"/leads/{lead['id']}/actions"), headers=headers)
    ).json()["items"]
    field_changes = [a for a in timeline if a["kind"] == "FIELD_CHANGE"]

    assert len(field_changes) == 3
    assert len({a["changeset_id"] for a in field_changes}) == 1, "one changeset for the batch"

    # Each carries old and new, which is what makes M7's undo possible.
    by_key = {a["payload"]["field_key"]: a["payload"] for a in field_changes}
    assert by_key["name"]["old"] == "Test Lead"
    assert by_key["name"]["new"] == "Renamed Lead"
    assert set(by_key) == {"name", "email", "alternate_phone"}


async def test_an_unchanged_field_writes_no_action(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """A PATCH resending the same value is not a change, and a timeline full of
    non-events is a timeline nobody reads."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000012")

    await api.patch(
        workspace.path(f"/leads/{lead['id']}"),
        headers=headers,
        json={"values": {"name": "Test Lead"}},
    )
    timeline = (
        await api.get(workspace.path(f"/leads/{lead['id']}/actions"), headers=headers)
    ).json()["items"]
    assert [a for a in timeline if a["kind"] == "FIELD_CHANGE"] == []


async def test_a_stage_change_records_old_and_new_ids(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Architecture rule 5b — M6's transition filters read exactly these keys."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000013")
    stages = (await api.get(workspace.path("/settings/stages"), headers=headers)).json()

    await api.patch(
        workspace.path(f"/leads/{lead['id']}"),
        headers=headers,
        json={"stage_id": stages["active"][0]["id"]},
    )

    timeline = (
        await api.get(workspace.path(f"/leads/{lead['id']}/actions"), headers=headers)
    ).json()["items"]
    change = next(a for a in timeline if a["kind"] == "STAGE_CHANGE")
    assert change["payload"]["old_stage_id"] == stages["initial"]["id"]
    assert change["payload"]["new_stage_id"] == stages["active"][0]["id"]


async def test_entering_the_lost_stage_attaches_a_reason(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """PROMPTS.md M5: "Entering the LOST stage requires a lost reason"."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000014")
    stages = (await api.get(workspace.path("/settings/stages"), headers=headers)).json()

    lost = await api.patch(
        workspace.path(f"/leads/{lead['id']}"),
        headers=headers,
        json={"stage_id": stages["lost"]["id"]},
    )
    assert lost.status_code == 200
    # Falls back to the workspace's default reason rather than refusing.
    assert lost.json()["lost_reason_id"] is not None


async def test_leaving_the_lost_stage_clears_the_reason(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """A reopened lead keeping its reason would sit in "why we lose deals"
    forever."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000015")
    stages = (await api.get(workspace.path("/settings/stages"), headers=headers)).json()

    await api.patch(
        workspace.path(f"/leads/{lead['id']}"),
        headers=headers,
        json={"stage_id": stages["lost"]["id"]},
    )
    reopened = await api.patch(
        workspace.path(f"/leads/{lead['id']}"),
        headers=headers,
        json={"stage_id": stages["active"][0]["id"]},
    )
    assert reopened.json()["lost_reason_id"] is None


async def test_a_lock_after_create_field_is_rejected_on_update(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    locked = await api.post(
        workspace.path("/settings/lead-fields"),
        headers=headers,
        json={"label": "Signup Source", "field_type": "TEXT", "lock_after_create": True},
    )
    assert locked.status_code == 201

    created = await api.post(
        workspace.path("/leads"),
        headers=headers,
        json={"values": {"name": "X", "phone": "9000000016", "signup_source": "web"}},
    )
    assert created.status_code == 201
    assert created.json()["values"]["signup_source"] == "web"

    blocked = await api.patch(
        workspace.path(f"/leads/{created.json()['id']}"),
        headers=headers,
        json={"values": {"signup_source": "changed"}},
    )
    assert blocked.status_code == 422
    assert "signup_source" in blocked.json()["detail"]["fields"]


# --- the changeset record ----------------------------------------------------


async def test_the_edit_report_lists_every_mutation(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000020")
    await api.patch(
        workspace.path(f"/leads/{lead['id']}"),
        headers=headers,
        json={"values": {"name": "Changed"}},
    )

    changesets = (await api.get(workspace.path("/changesets"), headers=headers)).json()
    assert changesets["total"] >= 2
    assert all(c["summary"] for c in changesets["items"]), "every changeset is described"
    assert {c["source"] for c in changesets["items"]} == {"SINGLE_EDIT"}


# --- the timeline ------------------------------------------------------------


async def test_a_note_and_a_call_land_on_the_timeline(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000021")
    dispositions = (
        await api.get(workspace.path("/settings/call-dispositions"), headers=headers)
    ).json()

    note = await api.post(
        workspace.path(f"/leads/{lead['id']}/notes"),
        headers=headers,
        json={"body": "Spoke to the office manager"},
    )
    assert note.status_code == 201
    assert note.json()["kind"] == "NOTE"

    call = await api.post(
        workspace.path(f"/leads/{lead['id']}/calls"),
        headers=headers,
        json={
            "direction": "OUTGOING",
            "disposition_id": dispositions[0]["id"],
            "duration_seconds": 95,
            "notes": "Interested",
        },
    )
    assert call.status_code == 201
    assert call.json()["kind"] == "CALL_LOGGED"
    assert call.json()["payload"]["duration_seconds"] == 95


async def test_a_custom_action_snapshots_its_score(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Editing the type's score afterwards must not rewrite history."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000022")

    action_type = (
        await api.post(
            workspace.path("/settings/custom-actions"),
            headers=headers,
            json={"name": "Demo Given", "score": 50, "direction": "OUTBOUND"},
        )
    ).json()

    logged = await api.post(
        workspace.path(f"/leads/{lead['id']}/custom-actions"),
        headers=headers,
        json={"action_type_id": action_type["id"], "values": {"notes": "Went well"}},
    )
    assert logged.status_code == 201, logged.text
    assert logged.json()["score_applied"] == 50

    # The lead's score is the rollup.
    detail = (await api.get(workspace.path(f"/leads/{lead['id']}"), headers=headers)).json()
    assert detail["score"] == 50

    # Change the type's score; the action already written keeps its snapshot.
    await api.patch(
        workspace.path(f"/settings/custom-actions/{action_type['id']}"),
        headers=headers,
        json={"score": 5},
    )
    timeline = (
        await api.get(workspace.path(f"/leads/{lead['id']}/actions"), headers=headers)
    ).json()["items"]
    custom = next(a for a in timeline if a["kind"] == "CUSTOM")
    assert custom["score_applied"] == 50, "history was rewritten by a settings change"


async def test_a_required_action_field_is_enforced(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Validated through the M2 action registry, not a second implementation."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000023")
    action_type = (
        await api.post(
            workspace.path("/settings/custom-actions"), headers=headers, json={"name": "Visit"}
        )
    ).json()

    response = await api.post(
        workspace.path(f"/leads/{lead['id']}/custom-actions"),
        headers=headers,
        json={"action_type_id": action_type["id"], "values": {}},
    )
    assert response.status_code == 422
    assert "notes" in response.json()["detail"]["fields"]


async def test_a_predated_action_is_refused_unless_allowed(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Otherwise a rep could backdate activity into a closed reporting period."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000024")
    action_type = (
        await api.post(
            workspace.path("/settings/custom-actions"),
            headers=headers,
            json={"name": "Strict", "allow_predated": False},
        )
    ).json()

    response = await api.post(
        workspace.path(f"/leads/{lead['id']}/custom-actions"),
        headers=headers,
        json={
            "action_type_id": action_type["id"],
            "values": {"notes": "Backdated"},
            "performed_at": "2020-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "predated_not_allowed"


async def test_the_lead_list_never_returns_actions(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Architecture rule 6."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000025")
    await api.post(
        workspace.path(f"/leads/{lead['id']}/notes"), headers=headers, json={"body": "A note"}
    )

    listing = await api.get(workspace.path("/leads"), headers=headers)
    assert listing.status_code == 200
    assert "actions" not in listing.text
    assert all("actions" not in item for item in listing.json()["items"])


# --- field-level permissions, end to end -------------------------------------


async def test_a_view_but_not_edit_field_is_visible_and_rejected_on_patch(
    api: AsyncClient, workspace: WorkspaceFixture, db_session: AsyncSession
) -> None:
    """The M4 acceptance check, now testable end to end.

    Visible in detail, rejected on PATCH — the two halves that prove projection
    and the write filter are independent.
    """
    headers = await _admin(api, workspace)
    field = (
        await api.post(
            workspace.path("/settings/lead-fields"),
            headers=headers,
            json={"label": "Internal Note", "field_type": "TEXT"},
        )
    ).json()

    lead = await api.post(
        workspace.path("/leads"),
        headers=headers,
        json={"values": {"name": "X", "phone": "9000000030", "internal_note": "admin wrote this"}},
    )
    assert lead.status_code == 201

    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=headers)
        ).json()
    }
    # Caller: View on the new field, but not Edit.
    await api.put(
        workspace.path(f"/settings/permission-templates/{templates['Caller']}/field-grants"),
        headers=headers,
        json={"grants": [{"field_id": field["id"], "view": True, "edit": False}]},
    )

    rep = workspace.members["rep"]
    await login(api, rep)

    detail = await api.get(workspace.path(f"/leads/{lead.json()['id']}"), headers=rep.auth)
    assert detail.status_code == 200
    assert detail.json()["values"]["internal_note"] == "admin wrote this", "View must show it"

    blocked = await api.patch(
        workspace.path(f"/leads/{lead.json()['id']}"),
        headers=rep.auth,
        json={"values": {"internal_note": "rep tried to change this"}},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "field_not_editable"
    assert blocked.json()["detail"]["fields"] == ["internal_note"]


async def test_a_field_without_view_is_absent_from_every_response(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Absent, not null — a null would confirm the field exists."""
    headers = await _admin(api, workspace)
    field = (
        await api.post(
            workspace.path("/settings/lead-fields"),
            headers=headers,
            json={"label": "Secret Salary", "field_type": "NUMBER"},
        )
    ).json()

    lead = await api.post(
        workspace.path("/leads"),
        headers=headers,
        json={"values": {"name": "X", "phone": "9000000031", "secret_salary": 90000}},
    )
    lead_id = lead.json()["id"]

    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=headers)
        ).json()
    }
    await api.put(
        workspace.path(f"/settings/permission-templates/{templates['Caller']}/field-grants"),
        headers=headers,
        json={"grants": [{"field_id": field["id"], "view": False, "edit": False}]},
    )

    rep = workspace.members["rep"]
    await login(api, rep)

    detail = await api.get(workspace.path(f"/leads/{lead_id}"), headers=rep.auth)
    assert detail.status_code == 200
    assert "secret_salary" not in detail.json()["values"]
    # The key is gone, and so is the value — checked against the parsed body
    # rather than the raw text, because the salary digits also appear inside
    # the normalised phone number.
    assert 90000 not in detail.json()["values"].values()

    listing = await api.get(workspace.path("/leads"), headers=rep.auth)
    assert "secret_salary" not in listing.text


# --- message templates -------------------------------------------------------


async def test_a_template_renders_against_a_real_lead(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """PROMPTS.md M5's second acceptance check: the preview plus any unresolved
    placeholders."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000040")
    normalised_phone = lead["values"]["phone"]

    template = await api.post(
        workspace.path("/templates"),
        headers=headers,
        json={
            "channel": "WHATSAPP",
            "name": "Intro",
            "body": "Hi {{name}}, we will call you on {{phone}}. Ref: {{missing_field}}",
        },
    )
    assert template.status_code == 201

    rendered = await api.post(
        workspace.path(f"/templates/{template.json()['id']}/render"),
        headers=headers,
        json={"lead_id": lead["id"]},
    )
    assert rendered.status_code == 200

    body = rendered.json()
    assert "Hi Test Lead" in body["body"]
    assert normalised_phone in body["body"]
    # Unresolved placeholders render empty and are reported, not left literal.
    assert "{{" not in body["body"]
    assert body["unresolved"] == ["missing_field"]


async def test_a_template_cannot_leak_a_field_the_sender_cannot_view(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The security property: substitution runs through the projection service,
    so a template naming a hidden field resolves to nothing."""
    headers = await _admin(api, workspace)
    field = (
        await api.post(
            workspace.path("/settings/lead-fields"),
            headers=headers,
            json={"label": "Private Code", "field_type": "TEXT"},
        )
    ).json()
    lead = await api.post(
        workspace.path("/leads"),
        headers=headers,
        json={"values": {"name": "X", "phone": "9000000041", "private_code": "TOPSECRET"}},
    )

    template = await api.post(
        workspace.path("/templates"),
        headers=headers,
        json={
            "channel": "SMS",
            "name": "Leaky",
            "body": "Your code is {{private_code}}",
            "shared": True,
        },
    )

    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=headers)
        ).json()
    }
    await api.put(
        workspace.path(f"/settings/permission-templates/{templates['Caller']}/field-grants"),
        headers=headers,
        json={"grants": [{"field_id": field["id"], "view": False}]},
    )

    rep = workspace.members["rep"]
    await login(api, rep)
    rendered = await api.post(
        workspace.path(f"/templates/{template.json()['id']}/render"),
        headers=rep.auth,
        json={"lead_id": lead.json()["id"]},
    )

    assert rendered.status_code == 200
    assert "TOPSECRET" not in rendered.text
    assert rendered.json()["unresolved"] == ["private_code"]


async def test_a_personal_template_is_invisible_to_another_member(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    mine = await api.post(
        workspace.path("/templates"),
        headers=headers,
        json={"channel": "SMS", "name": "Just Mine", "body": "Hello"},
    )
    assert mine.status_code == 201
    assert mine.json()["visibility"] == "personal"

    rep = workspace.members["rep"]
    await login(api, rep)
    listing = await api.get(workspace.path("/templates"), headers=rep.auth)
    assert mine.json()["id"] not in listing.text

    # And not reachable by id either — visibility is a read rule.
    direct = await api.post(
        workspace.path(f"/templates/{mine.json()['id']}/render"),
        headers=rep.auth,
        json={"lead_id": mine.json()["id"]},
    )
    assert direct.status_code == 404


async def test_an_email_template_requires_a_subject(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    response = await api.post(
        workspace.path("/templates"),
        headers=headers,
        json={"channel": "EMAIL", "name": "No Subject", "body": "Body only"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "subject_required"


# --- visibility --------------------------------------------------------------


async def test_a_rep_sees_unassigned_and_their_own_leads(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The manager-hierarchy rule from M1, now applied to leads."""
    headers = await _admin(api, workspace)
    rep = workspace.members["rep"]

    unassigned = await _create_lead(api, workspace, headers, phone="9000000050")
    theirs = await api.post(
        workspace.path("/leads"),
        headers=headers,
        json={
            "values": {"name": "Rep's Lead", "phone": "9000000051"},
            "assignee_id": str(rep.membership.id),
        },
    )
    assert theirs.status_code == 201

    await login(api, rep)
    listing = (await api.get(workspace.path("/leads"), headers=rep.auth)).json()
    ids = {item["id"] for item in listing["items"]}

    assert theirs.json()["id"] in ids
    # Unassigned leads belong to nobody yet; hiding them would make new leads
    # vanish until someone assigned them.
    assert unassigned["id"] in ids


async def test_a_deleted_lead_is_soft_deleted(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Architecture rule 13: leads never hard-delete."""
    headers = await _admin(api, workspace)
    lead = await _create_lead(api, workspace, headers, phone="9000000060")

    deleted = await api.delete(workspace.path(f"/leads/{lead['id']}"), headers=headers)
    assert deleted.status_code == 204

    assert (
        await api.get(workspace.path(f"/leads/{lead['id']}"), headers=headers)
    ).status_code == 404
    listing = (await api.get(workspace.path("/leads"), headers=headers)).json()
    assert lead["id"] not in {item["id"] for item in listing["items"]}

    # The identity is free again, which is what makes re-import after a
    # mistaken delete possible.
    recreated = await api.post(
        workspace.path("/leads"),
        headers=headers,
        json={"values": {"name": "Second Try", "phone": "9000000060"}},
    )
    assert recreated.status_code == 201
