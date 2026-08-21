"""Sales groups, assignment rules and distribution over HTTP (M8).

The engine has its own tests; this covers the endpoints — the permission gates,
the validation that keeps a broken rule out of the lead-create path, and the two
properties an operator relies on: a preview assigns nothing, and a distribution
undoes as a unit.
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
async def ws(
    db_session: AsyncSession, hasher: PasswordHasherService, api: AsyncClient
) -> WorkspaceFixture:
    fixture = await build_workspace(
        db_session, hasher, name="Routing API Co", owner_email="routing-owner@example.com"
    )
    await login(api, fixture.owner)
    return fixture


async def _rep(
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    ws: WorkspaceFixture,
    api: AsyncClient,
    *,
    key: str,
) -> Any:
    actor = await add_member(
        db_session,
        hasher,
        ws,
        key=key,
        email=f"routing-{key}@example.com",
        template_name="Caller",
    )
    await login(api, actor)
    return actor


async def _lead(api: AsyncClient, ws: WorkspaceFixture, phone: str) -> dict[str, Any]:
    response = await api.post(
        ws.path("/leads"),
        headers=ws.owner.auth,
        json={"values": {"name": "Lead", "phone": phone}},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- sales groups ------------------------------------------------------------


async def test_a_group_round_trips_with_weighted_members(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    rep = await _rep(db_session, hasher, ws, api, key="one")

    created = await api.post(
        ws.path("/settings/sales-groups"),
        headers=ws.owner.auth,
        json={"name": "Inbound", "description": "Takes web enquiries"},
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["id"]

    members = await api.put(
        ws.path(f"/settings/sales-groups/{group_id}/members"),
        headers=ws.owner.auth,
        json=[{"membership_id": str(rep.membership.id), "weight": 3}],
    )
    assert members.status_code == 200, members.text
    assert members.json() == [{"membership_id": str(rep.membership.id), "weight": 3}]


async def test_a_duplicate_group_name_is_refused(api: AsyncClient, ws: WorkspaceFixture) -> None:
    body = {"name": "Inbound"}
    first = await api.post(ws.path("/settings/sales-groups"), headers=ws.owner.auth, json=body)
    assert first.status_code == 201
    second = await api.post(ws.path("/settings/sales-groups"), headers=ws.owner.auth, json=body)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "duplicate_group"


async def test_archiving_a_group_hides_it_but_keeps_it(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """Archive, never delete — a rule may still point at it."""
    created = await api.post(
        ws.path("/settings/sales-groups"), headers=ws.owner.auth, json={"name": "Old team"}
    )
    group_id = created.json()["id"]

    assert (
        await api.delete(ws.path(f"/settings/sales-groups/{group_id}"), headers=ws.owner.auth)
    ).status_code == 204

    listed = await api.get(ws.path("/settings/sales-groups"), headers=ws.owner.auth)
    assert [g["name"] for g in listed.json()] == []

    with_archived = await api.get(
        ws.path("/settings/sales-groups"),
        headers=ws.owner.auth,
        params={"include_archived": True},
    )
    assert [g["name"] for g in with_archived.json()] == ["Old team"]


# --- assignment rules --------------------------------------------------------


async def test_a_rule_is_validated_against_its_strategy_on_write(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """A round-robin rule with no members is refused here, not at 3am.

    `config` is loose JSONB because each strategy reads different keys. That
    looseness has to be paid for somewhere; the cheapest place is the moment an
    admin saves the rule.
    """
    response = await api.post(
        ws.path("/settings/assignment-rules"),
        headers=ws.owner.auth,
        json={"name": "Broken", "strategy": "ROUND_ROBIN", "config": {}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_config"


async def test_a_rule_with_malformed_conditions_is_refused(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """A rule the engine could not evaluate is a lead-create path that raises."""
    response = await api.post(
        ws.path("/settings/assignment-rules"),
        headers=ws.owner.auth,
        json={
            "name": "Nonsense",
            "strategy": "UNASSIGNED",
            "config": {},
            "conditions": {"type": "field", "key": "name", "op": "not_an_operator"},
        },
    )
    assert response.status_code == 422, response.text


async def test_rules_reorder_as_a_whole_list(api: AsyncClient, ws: WorkspaceFixture) -> None:
    ids = []
    for name in ("First", "Second", "Third"):
        created = await api.post(
            ws.path("/settings/assignment-rules"),
            headers=ws.owner.auth,
            json={"name": name, "strategy": "UNASSIGNED", "config": {}},
        )
        assert created.status_code == 201, created.text
        ids.append(created.json()["id"])

    reordered = await api.patch(
        ws.path("/settings/assignment-rules/reorder"),
        headers=ws.owner.auth,
        json={"order": [ids[2], ids[0], ids[1]]},
    )
    assert reordered.status_code == 200, reordered.text
    assert [r["name"] for r in reordered.json()] == ["Third", "First", "Second"]


async def test_a_partial_reorder_is_refused(api: AsyncClient, ws: WorkspaceFixture) -> None:
    """Omitting a rule would silently renumber it."""
    created = await api.post(
        ws.path("/settings/assignment-rules"),
        headers=ws.owner.auth,
        json={"name": "Only", "strategy": "UNASSIGNED", "config": {}},
    )
    await api.post(
        ws.path("/settings/assignment-rules"),
        headers=ws.owner.auth,
        json={"name": "Other", "strategy": "UNASSIGNED", "config": {}},
    )
    response = await api.patch(
        ws.path("/settings/assignment-rules/reorder"),
        headers=ws.owner.auth,
        json={"order": [created.json()["id"]]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "incomplete_order"


async def test_deleting_a_rule_deactivates_it_and_keeps_the_history(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    created = await api.post(
        ws.path("/settings/assignment-rules"),
        headers=ws.owner.auth,
        json={"name": "Retired", "strategy": "UNASSIGNED", "config": {}},
    )
    rule_id = created.json()["id"]
    assert (
        await api.delete(ws.path(f"/settings/assignment-rules/{rule_id}"), headers=ws.owner.auth)
    ).status_code == 204

    listed = await api.get(ws.path("/settings/assignment-rules"), headers=ws.owner.auth)
    rows = {r["id"]: r for r in listed.json()}
    assert rows[rule_id]["is_active"] is False, "the rule explains where old leads went"


async def test_a_new_lead_is_routed_by_the_matching_rule(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """The end-to-end acceptance check, minus the intake API M10 brings."""
    rep = await _rep(db_session, hasher, ws, api, key="router")
    created = await api.post(
        ws.path("/settings/assignment-rules"),
        headers=ws.owner.auth,
        json={
            "name": "Everything to the rep",
            "strategy": "FIXED",
            "config": {"membership_id": str(rep.membership.id)},
        },
    )
    assert created.status_code == 201, created.text

    lead = await _lead(api, ws, "+19995551111")
    assert lead["assignee_id"] == str(rep.membership.id)


async def test_an_explicit_assignee_beats_the_rules(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """Naming a rep is a decision, not a default for the rules to override."""
    rep = await _rep(db_session, hasher, ws, api, key="rules")
    await api.post(
        ws.path("/settings/assignment-rules"),
        headers=ws.owner.auth,
        json={
            "name": "Everything to the rep",
            "strategy": "FIXED",
            "config": {"membership_id": str(rep.membership.id)},
        },
    )

    response = await api.post(
        ws.path("/leads"),
        headers=ws.owner.auth,
        json={
            "values": {"name": "Hand placed", "phone": "+19995552222"},
            "assignee_id": str(ws.owner.membership.id),
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["assignee_id"] == str(ws.owner.membership.id)


async def test_preview_assigns_nothing(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    rep = await _rep(db_session, hasher, ws, api, key="preview")
    await api.post(
        ws.path("/settings/assignment-rules"),
        headers=ws.owner.auth,
        json={
            "name": "Would route",
            "strategy": "FIXED",
            "config": {"membership_id": str(rep.membership.id)},
            "is_active": False,
        },
    )
    lead = await _lead(api, ws, "+19995553333")
    assert lead["assignee_id"] is None

    # Reactivate, then preview: it reports the pick without making it.
    listed = await api.get(ws.path("/settings/assignment-rules"), headers=ws.owner.auth)
    rule_id = listed.json()[0]["id"]
    await api.patch(
        ws.path(f"/settings/assignment-rules/{rule_id}"),
        headers=ws.owner.auth,
        json={"is_active": True},
    )

    preview = await api.post(
        ws.path("/settings/assignment-rules/preview"),
        headers=ws.owner.auth,
        params={"lead_id": lead["id"]},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["membership_id"] == str(rep.membership.id)
    assert preview.json()["reason"] == "matched"

    unchanged = await api.get(ws.path(f"/leads/{lead['id']}"), headers=ws.owner.auth)
    assert unchanged.json()["assignee_id"] is None, "a preview must assign nothing"


# --- distribution ------------------------------------------------------------


async def test_a_distribution_is_one_changeset_and_undoes_as_a_unit(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """Moving 500 leads onto the wrong rep has to be takeable-back."""
    rep = await _rep(db_session, hasher, ws, api, key="dist")
    leads = [await _lead(api, ws, f"+199955540{i:02d}") for i in range(3)]

    response = await api.post(
        ws.path("/leads/distribute"),
        headers=ws.owner.auth,
        json={
            "lead_ids": [lead["id"] for lead in leads],
            "strategy": "FIXED",
            "config": {"membership_id": str(rep.membership.id)},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["assigned"] == 3
    assert payload["changeset_id"] is not None

    for lead in leads:
        current = await api.get(ws.path(f"/leads/{lead['id']}"), headers=ws.owner.auth)
        assert current.json()["assignee_id"] == str(rep.membership.id)

    undone = await api.post(
        ws.path(f"/changesets/{payload['changeset_id']}/undo"),
        headers=ws.owner.auth,
        json={"skip_conflicts": False},
    )
    assert undone.status_code == 200, undone.text

    for lead in leads:
        current = await api.get(ws.path(f"/leads/{lead['id']}"), headers=ws.owner.auth)
        assert current.json()["assignee_id"] is None


async def test_distribution_refuses_more_leads_than_one_changeset_should_hold(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    response = await api.post(
        ws.path("/leads/distribute"),
        headers=ws.owner.auth,
        json={
            "lead_ids": [f"00000000-0000-4000-8000-{i:012d}" for i in range(501)],
            "strategy": "UNASSIGNED",
            "config": {},
        },
    )
    assert response.status_code == 422


# --- permission gates --------------------------------------------------------


async def test_a_caller_cannot_manage_rules_or_distribute(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """Redirecting every incoming lead is not a caller's decision."""
    rep = await _rep(db_session, hasher, ws, api, key="nosy")

    created = await api.post(
        ws.path("/settings/assignment-rules"),
        headers=rep.auth,
        json={"name": "Mine now", "strategy": "UNASSIGNED", "config": {}},
    )
    assert created.status_code == 403
    assert created.json()["detail"]["code"] == "insufficient_permissions"

    distributed = await api.post(
        ws.path("/leads/distribute"),
        headers=rep.auth,
        json={
            "lead_ids": ["00000000-0000-4000-8000-000000000001"],
            "strategy": "UNASSIGNED",
            "config": {},
        },
    )
    assert distributed.status_code == 403


async def test_a_caller_cannot_schedule_a_report(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """A schedule mails rendered lead data to arbitrary addresses."""
    rep = await _rep(db_session, hasher, ws, api, key="mailer")
    response = await api.post(
        ws.path("/scheduled-reports"),
        headers=rep.auth,
        json={
            "name": "Everything",
            "report_type": "leads",
            "cron": "0 9 * * *",
            "recipients": ["elsewhere@example.com"],
        },
    )
    assert response.status_code == 403


async def test_scheduling_an_unknown_report_type_is_refused(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """A schedule for a report that does not exist is a row that fails every
    morning, so the write path refuses it by name.

    M8 shipped with `leads` alone and this test used `leaderboard`; M9 added the
    reports, so the catalogue grew and the example had to move to something
    genuinely absent. The check is the same one — it is the list that changed.
    """
    response = await api.post(
        ws.path("/scheduled-reports"),
        headers=ws.owner.auth,
        json={
            "name": "Not yet",
            "report_type": "astrology",
            "cron": "0 9 * * *",
            "recipients": ["ops@example.com"],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_report_type"


async def test_run_now_sends_immediately(
    api: AsyncClient, ws: WorkspaceFixture, wired_app: FastAPI
) -> None:
    from app.services.email import RecordingEmailSender

    sender = RecordingEmailSender()
    wired_app.state.email_sender = sender

    created = await api.post(
        ws.path("/scheduled-reports"),
        headers=ws.owner.auth,
        json={
            "name": "On demand",
            "report_type": "leads",
            "cron": "0 9 * * *",
            "recipients": ["ops@example.com"],
        },
    )
    assert created.status_code == 201, created.text

    ran = await api.post(
        ws.path(f"/scheduled-reports/{created.json()['id']}/run-now"),
        headers=ws.owner.auth,
    )
    assert ran.status_code == 200, ran.text
    assert ran.json()["last_run_at"] is not None
    assert len(sender.sent) == 1
    assert sender.sent[0].to == ("ops@example.com",)
