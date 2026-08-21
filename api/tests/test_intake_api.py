"""The inbound intake API (M10).

`docs/02-api-contract.md` states the rule this whole surface is shaped around:
**a rejected payload at 2am is a lost lead.** So the tests that matter most here
are the ones proving the API *doesn't* reject — an unknown field is stored and
reported, an unrecognised assignee degrades to the assignment rules, a bad row in
a batch does not sink the other 499.

The exceptions are deliberate and each has a test too: an unknown stage is a 400
(silently filing a lead in the wrong pipeline position is worse than saying so),
and every outcome — rejections included — lands in the intake log.
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
        db_session, hasher, name="Intake Co", owner_email="intake-owner@example.com"
    )
    await login(api, fixture.owner)
    return fixture


async def _key(api: AsyncClient, ws: WorkspaceFixture, *, template: str = "Root") -> str:
    response = await api.post(
        ws.path("/settings/api-keys"),
        headers=ws.owner.auth,
        json={
            "name": f"Integration {template}",
            "permission_template_id": str(ws.templates[template].id),
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["key"])


def _headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


async def _post(
    api: AsyncClient, key: str, body: dict[str, Any], path: str = "/api/v1/intake/leads"
) -> Any:
    return await api.post(path, headers=_headers(key), json=body)


# --- the rule that is not negotiable -----------------------------------------


async def test_an_unknown_field_is_stored_and_warned_about_never_rejected(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """A rejected payload at 2am is a lost lead.

    The integration that starts sending `utm_campaign` next Tuesday must not
    take the pipeline down with it. The value is kept and the warning is how the
    operator finds out to make it a real field.
    """
    key = await _key(api, ws)
    response = await _post(
        api,
        key,
        {
            "identity": "+919000000101",
            "values": {"name": "From a form", "utm_campaign": "spring-sale"},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["outcome"] == "CREATED"
    assert any("utm_campaign" in warning for warning in payload["warnings"])

    lead = await api.get(ws.path(f"/leads/{payload['lead_id']}"), headers=ws.owner.auth)
    assert lead.status_code == 200
    assert lead.json()["values"].get("utm_campaign") == "spring-sale", (
        "the unknown value was warned about but not actually stored"
    )


async def test_an_unrecognised_assignee_degrades_to_the_rules(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """Losing the routing is bad; losing the lead is worse."""
    key = await _key(api, ws)
    response = await _post(
        api,
        key,
        {
            "identity": "+919000000102",
            "values": {"name": "Misrouted"},
            "assignee_email": "nobody@example.com",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "CREATED"
    assert any("nobody@example.com" in w for w in response.json()["warnings"])


async def test_an_unknown_stage_is_refused_and_logged(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """The deliberate exception.

    Silently filing a lead at the wrong pipeline position corrupts every funnel
    report downstream, and unlike an unknown field there is no safe place to put
    it. So this one says so — and the rejection is still logged, because "we
    posted it and nothing arrived" is the question the log exists to answer.
    """
    key = await _key(api, ws)
    response = await _post(
        api,
        key,
        {"identity": "+919000000103", "values": {"name": "Lost"}, "stage": "Nowhere"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unknown_stage"

    log = await api.get(ws.path("/settings/intake-log"), headers=ws.owner.auth)
    assert log.status_code == 200, log.text
    entries = log.json()["items"]
    assert entries[0]["outcome"] == "REJECTED"
    assert entries[0]["status_code"] == 400
    assert "Nowhere" in (entries[0]["error"] or "")


# --- dedupe ------------------------------------------------------------------


async def test_dedupe_update_merges_without_blanking(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """A form that only collected a phone must not wipe the name."""
    key = await _key(api, ws)
    first = await _post(
        api,
        key,
        {"identity": "+919000000104", "values": {"name": "Full Record", "email": "a@example.com"}},
    )
    assert first.status_code == 200, first.text
    lead_id = first.json()["lead_id"]

    second = await _post(
        api, key, {"identity": "+919000000104", "values": {"email": "b@example.com"}}
    )
    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "UPDATED"
    assert second.json()["lead_id"] == lead_id

    lead = await api.get(ws.path(f"/leads/{lead_id}"), headers=ws.owner.auth)
    values = lead.json()["values"]
    assert values["email"] == "b@example.com"
    assert values["name"] == "Full Record", "a partial payload blanked existing data"


async def test_dedupe_skip_leaves_the_existing_lead_alone(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    key = await _key(api, ws)
    first = await _post(api, key, {"identity": "+919000000105", "values": {"name": "Original"}})
    lead_id = first.json()["lead_id"]

    second = await _post(
        api,
        key,
        {"identity": "+919000000105", "values": {"name": "Overwrite"}, "dedupe": "skip"},
    )
    assert second.json()["outcome"] == "SKIPPED"
    assert second.json()["lead_id"] == lead_id

    lead = await api.get(ws.path(f"/leads/{lead_id}"), headers=ws.owner.auth)
    assert lead.json()["values"]["name"] == "Original"


async def test_create_duplicate_says_why_it_cannot(api: AsyncClient, ws: WorkspaceFixture) -> None:
    """The identity field is unique by construction, so the mode is impossible.

    Saying so beats silently doing something else, which is what an integration
    author would otherwise have to reverse-engineer from the data.
    """
    key = await _key(api, ws)
    await _post(api, key, {"identity": "+919000000106", "values": {"name": "One"}})
    response = await _post(
        api,
        key,
        {
            "identity": "+919000000106",
            "values": {"name": "Two"},
            "dedupe": "create_duplicate",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "identity_exists"


async def test_identity_matching_normalises_first(api: AsyncClient, ws: WorkspaceFixture) -> None:
    """Matching the raw string would miss every existing lead.

    The stored identity is normalised with the workspace's country code, so an
    integration sending a differently formatted number must still find the same
    lead rather than creating a second one.
    """
    key = await _key(api, ws)
    first = await _post(api, key, {"identity": "+919000000107", "values": {"name": "Normalised"}})
    second = await _post(api, key, {"identity": "9000000107", "values": {"name": "Same person"}})
    assert second.json()["outcome"] == "UPDATED"
    assert second.json()["lead_id"] == first.json()["lead_id"]


# --- assignment on intake ----------------------------------------------------


async def test_assignment_rules_run_on_intake(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    """The M8 acceptance criterion's missing half.

    The rules live in `create_lead`, which this calls — one implementation, three
    callers, which was the point of putting them there rather than in a router.
    """
    rep = await add_member(
        db_session,
        hasher,
        ws,
        key="rep",
        email="intake-rep@example.com",
        template_name="Caller",
    )
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

    key = await _key(api, ws)
    response = await _post(api, key, {"identity": "+919000000108", "values": {"name": "Routed"}})
    assert response.status_code == 200, response.text

    lead = await api.get(ws.path(f"/leads/{response.json()['lead_id']}"), headers=ws.owner.auth)
    assert lead.json()["assignee_id"] == str(rep.membership.id)


# --- batches -----------------------------------------------------------------


async def test_one_bad_row_does_not_sink_the_batch(api: AsyncClient, ws: WorkspaceFixture) -> None:
    """A malformed record in a nightly export must not lose the other 499."""
    key = await _key(api, ws)
    response = await _post(
        api,
        key,
        {
            "leads": [
                {"identity": "+919000000201", "values": {"name": "Good one"}},
                {"identity": "+919000000202", "values": {"name": "Bad"}, "stage": "Nope"},
                {"identity": "+919000000203", "values": {"name": "Good two"}},
            ]
        },
        path="/api/v1/intake/leads/batch",
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["created"] == 2
    assert payload["rejected"] == 1
    assert [r["outcome"] for r in payload["results"]] == [
        "CREATED",
        "REJECTED",
        "CREATED",
    ]


# --- authentication ----------------------------------------------------------


async def test_a_missing_or_wrong_key_is_a_401(api: AsyncClient, ws: WorkspaceFixture) -> None:
    """Every failure is the same message.

    Distinguishing "no such key" from "revoked" would tell a prober which of
    their guesses was once real.
    """
    body = {"identity": "+919000000109", "values": {"name": "Anonymous"}}

    without = await api.post("/api/v1/intake/leads", json=body)
    assert without.status_code == 401
    assert without.json()["detail"]["code"] == "invalid_api_key"

    wrong = await _post(api, "crmk_totally-made-up-key", body)
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "invalid_api_key"


async def test_a_revoked_key_stops_working(api: AsyncClient, ws: WorkspaceFixture) -> None:
    created = await api.post(
        ws.path("/settings/api-keys"),
        headers=ws.owner.auth,
        json={
            "name": "Doomed",
            "permission_template_id": str(ws.templates["Root"].id),
        },
    )
    key = created.json()["key"]
    assert (await api.get("/api/v1/intake/ping", headers=_headers(key))).status_code == 200

    revoked = await api.delete(
        ws.path(f"/settings/api-keys/{created.json()['id']}"), headers=ws.owner.auth
    )
    assert revoked.status_code == 204

    after = await api.get("/api/v1/intake/ping", headers=_headers(key))
    assert after.status_code == 401


async def test_the_key_plaintext_is_returned_once_and_never_again(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    created = await api.post(
        ws.path("/settings/api-keys"),
        headers=ws.owner.auth,
        json={"name": "Once", "permission_template_id": str(ws.templates["Root"].id)},
    )
    assert created.status_code == 201
    assert created.json()["key"].startswith("crmk_")

    listed = await api.get(ws.path("/settings/api-keys"), headers=ws.owner.auth)
    assert listed.status_code == 200
    row = listed.json()[0]
    assert "key" not in row, "the list endpoint leaked a key"
    assert "hashed_key" not in row
    # The prefix identifies it without reproducing it.
    assert created.json()["key"].startswith(row["prefix"])


async def test_ping_verifies_a_key_without_creating_anything(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """Otherwise the only way to test a key is to post a real lead, which is how
    test data ends up in a customer's pipeline."""
    key = await _key(api, ws)
    response = await api.get("/api/v1/intake/ping", headers=_headers(key))
    assert response.status_code == 200
    assert response.json()["workspace_id"] == str(ws.workspace.id)

    leads = await api.post(ws.path("/leads/search"), headers=ws.owner.auth, json={"limit": 5})
    assert leads.json()["total"] == 0


# --- the key's permission template -------------------------------------------


async def test_a_key_cannot_write_a_field_its_template_cannot_edit(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession
) -> None:
    """A key is not an admin bypass.

    This is the property `docs/06-voice-integration-contract.md` §3 depends on:
    a field the voice template cannot Edit is refused *by name* rather than
    silently dropped, so an integration author finds out at once.
    """
    from app.models import LeadField, LeadFieldType

    ungranted = LeadField(
        workspace_id=ws.workspace.id,
        key="salary",
        label="Salary",
        field_type=LeadFieldType.NUMBER,
        sort_order=99,
    )
    db_session.add(ungranted)
    await db_session.commit()

    key = await _key(api, ws, template="Caller")
    response = await _post(
        api,
        key,
        {"identity": "+919000000110", "values": {"name": "Nosy", "salary": 1000}},
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "field_not_editable"
