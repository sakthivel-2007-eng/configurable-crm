"""The filter DSL, its compiler, search, sorting and saved views (M6).

The two acceptance checks M6 states verbatim, both driven through the API:

- "leads whose status went from HOT to Lost in the last 7 days"
- "leads with no outgoing call in 14 days"

Everything else here exists to pin down the parts that are easy to get subtly
wrong: that operators come from the type registry rather than a second table,
that a field the caller cannot View is not filterable, that sorting is refused
on an unindexed field with a message naming the fix, and that sharing a filter
never shares a lead.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import WorkspaceFixture, add_member, build_workspace, login

from app.auth.passwords import PasswordHasherService
from app.models.lead import Action, Lead

pytestmark = pytest.mark.integration

_EMPTY: dict[str, Any] = {"type": "group", "op": "AND", "children": []}


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


@pytest.fixture
async def workspace(db_session: AsyncSession, hasher: PasswordHasherService) -> WorkspaceFixture:
    fixture = await build_workspace(
        db_session, hasher, name="Filters", owner_email="filters-owner@example.com"
    )
    await add_member(
        db_session,
        hasher,
        fixture,
        key="rep",
        email="filters-rep@example.com",
        template_name="Caller",
    )
    # Declaring an indexed field runs CREATE INDEX CONCURRENTLY, which waits for
    # every open transaction — an idle-in-transaction fixture hangs the test
    # rather than failing it. See the same note in test_fields_api.
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


async def _search(
    api: AsyncClient, ws: WorkspaceFixture, headers: dict[str, str], **body: Any
) -> dict:
    response = await api.post(ws.path("/leads/search"), headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def _stages(
    api: AsyncClient, ws: WorkspaceFixture, headers: dict[str, str]
) -> dict[str, str]:
    """The workspace's pipeline, by structural kind rather than by label.

    Keyed on `initial`/`won`/`lost` because a test must not depend on what the
    workspace happens to call them — that is the customer's choice.
    """
    response = await api.get(ws.path("/settings/stages"), headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "initial": body["initial"]["id"],
        "won": body["won"]["id"],
        "lost": body["lost"]["id"],
    }


def _ids(page: dict) -> set[str]:
    return {item["id"] for item in page["items"]}


# --- field rules -------------------------------------------------------------


async def test_a_field_rule_filters_on_a_workspace_defined_field(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")

    match = await _lead(api, workspace, headers, name="A", phone="9300000001", city="Chennai")
    await _lead(api, workspace, headers, name="B", phone="9300000002", city="Mumbai")

    page = await _search(
        api,
        workspace,
        headers,
        filter={"type": "field", "key": "city", "op": "eq", "value": "Chennai"},
    )
    assert _ids(page) == {match["id"]}


async def test_numeric_operators_compare_numerically_not_as_text(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The bug a JSONB store invites: '9' > '10' is true as text, false as a number."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Budget", field_type="NUMBER")

    big = await _lead(api, workspace, headers, name="Big", phone="9300000010", budget=10)
    await _lead(api, workspace, headers, name="Small", phone="9300000011", budget=9)

    page = await _search(
        api, workspace, headers, filter={"type": "field", "key": "budget", "op": "gt", "value": 9.5}
    )
    assert _ids(page) == {big["id"]}


async def test_tags_use_set_operators(api: AsyncClient, workspace: WorkspaceFixture) -> None:
    headers = await _admin(api, workspace)
    field = await _field(api, workspace, headers, label="Interests", field_type="TAGS")
    for label in ("Alpha", "Beta"):
        created = await api.post(
            workspace.path(f"/settings/lead-fields/{field['id']}/options"),
            headers=headers,
            json={"label": label},
        )
        assert created.status_code == 201, created.text

    both = await _lead(
        api, workspace, headers, name="Both", phone="9300000020", interests=["alpha", "beta"]
    )
    one = await _lead(api, workspace, headers, name="One", phone="9300000021", interests=["alpha"])

    any_of = await _search(
        api,
        workspace,
        headers,
        filter={"type": "field", "key": "interests", "op": "has_any", "value": ["beta"]},
    )
    assert _ids(any_of) == {both["id"]}

    all_of = await _search(
        api,
        workspace,
        headers,
        filter={"type": "field", "key": "interests", "op": "has_all", "value": ["alpha", "beta"]},
    )
    assert _ids(all_of) == {both["id"]}
    assert one["id"] not in _ids(all_of)


async def test_nested_and_or_groups_compile(api: AsyncClient, workspace: WorkspaceFixture) -> None:
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")
    await _field(api, workspace, headers, label="Budget", field_type="NUMBER")

    wanted = await _lead(
        api, workspace, headers, name="Wanted", phone="9300000030", city="Chennai", budget=100
    )
    await _lead(
        api, workspace, headers, name="Wrong city", phone="9300000031", city="Delhi", budget=100
    )
    other = await _lead(
        api, workspace, headers, name="Other", phone="9300000032", city="Mumbai", budget=999
    )

    page = await _search(
        api,
        workspace,
        headers,
        filter={
            "type": "group",
            "op": "OR",
            "children": [
                {
                    "type": "group",
                    "op": "AND",
                    "children": [
                        {"type": "field", "key": "city", "op": "eq", "value": "Chennai"},
                        {"type": "field", "key": "budget", "op": "lte", "value": 500},
                    ],
                },
                {"type": "field", "key": "budget", "op": "gte", "value": 999},
            ],
        },
    )
    assert _ids(page) == {wanted["id"], other["id"]}


async def test_an_operator_the_type_does_not_support_is_refused(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Operators come from the registry, so this is a data question, not a list here."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Signed Up", field_type="CHECKBOX")

    response = await api.post(
        workspace.path("/leads/search"),
        headers=headers,
        json={"filter": {"type": "field", "key": "signed_up", "op": "gt", "value": 1}},
    )
    assert response.status_code == 422, response.text
    body = response.json()["detail"]
    assert body["code"] == "unsupported_operator"
    # The message tells the user what they *can* do, from the registry.
    assert "eq" in body["supported"]


async def test_an_empty_filter_matches_everything(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Clearing the builder must behave like never opening it."""
    headers = await _admin(api, workspace)
    await _lead(api, workspace, headers, name="Only", phone="9300000040")
    page = await _search(api, workspace, headers, filter=_EMPTY)
    assert page["total"] == 1


async def test_like_wildcards_in_a_search_value_are_escaped(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """`%` is a literal character to a user, not a wildcard."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Note", field_type="TEXT")
    await _lead(api, workspace, headers, name="Discount", phone="9300000050", note="50% off")
    await _lead(api, workspace, headers, name="Plain", phone="9300000051", note="nothing here")

    page = await _search(
        api,
        workspace,
        headers,
        filter={"type": "field", "key": "note", "op": "contains", "value": "%"},
    )
    assert page["total"] == 1, "an unescaped % would match every lead"


# --- field-level permissions -------------------------------------------------


async def test_filtering_on_a_field_the_caller_cannot_view_is_refused(
    api: AsyncClient, workspace: WorkspaceFixture, db_session: AsyncSession
) -> None:
    """Filtering a hidden field is a *read* of it.

    `stage = X AND salary > 100000` returning a count of 1 is an oracle over
    the hidden value, so the compiler refuses before it emits any SQL — and
    refuses with the same error as an absent field, since a distinct one would
    confirm the field exists.
    """
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Salary", field_type="NUMBER")
    await _lead(api, workspace, headers, name="Earner", phone="9300000060", salary=100000)

    rep = workspace.members["rep"]
    await login(api, rep)

    response = await api.post(
        workspace.path("/leads/search"),
        headers=rep.auth,
        json={"filter": {"type": "field", "key": "salary", "op": "gt", "value": 1}},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "unknown_field"
    assert "salary" in response.json()["detail"]["message"]


# --- history predicates ------------------------------------------------------


async def _log_call(
    api: AsyncClient,
    ws: WorkspaceFixture,
    headers: dict[str, str],
    lead_id: str,
    *,
    direction: str = "OUTGOING",
) -> dict:
    dispositions = (await api.get(ws.path("/settings/call-dispositions"), headers=headers)).json()
    response = await api.post(
        ws.path(f"/leads/{lead_id}/calls"),
        headers=headers,
        json={
            "direction": direction,
            "disposition_id": dispositions[0]["id"],
            "duration_seconds": 60,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_no_outgoing_call_in_fourteen_days(
    api: AsyncClient, workspace: WorkspaceFixture, db_session: AsyncSession
) -> None:
    """M6's second acceptance check, and the filter a telecalling CRM lives on.

    Three leads make the semantics explicit: the window means "not *recently*",
    not "never", so a lead called long ago is neglected and must come back.
    """
    headers = await _admin(api, workspace)
    called_now = await _lead(api, workspace, headers, name="Called today", phone="9310000001")
    never_called = await _lead(api, workspace, headers, name="Never called", phone="9310000002")
    called_long_ago = await _lead(api, workspace, headers, name="Stale", phone="9310000003")

    await _log_call(api, workspace, headers, called_now["id"])
    stale_call = await _log_call(api, workspace, headers, called_long_ago["id"])
    # Backdate past the window. The API refuses predated calls, so history is
    # arranged directly — this is the state a real workspace reaches by waiting.
    await db_session.execute(
        update(Action)
        .where(Action.id == stale_call["id"])
        .values(performed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=40))
    )
    await db_session.commit()

    # An incoming call is not an outgoing one: the payload match is what makes
    # the difference, and the DSL never learns what a "call" is.
    await _log_call(api, workspace, headers, never_called["id"], direction="INCOMING")

    page = await _search(
        api,
        workspace,
        headers,
        filter={
            "type": "action_not_performed",
            "action_kind": "CALL_LOGGED",
            "payload_match": {"direction": "OUTGOING"},
            "within": {"last_days": 14},
        },
    )
    assert _ids(page) == {never_called["id"], called_long_ago["id"]}


async def test_status_went_from_one_stage_to_lost_in_the_last_seven_days(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """M6's first acceptance check.

    The stage is *created by the admin at runtime* and named by them — the
    product ships no such stage, which is the whole point of the pipeline being
    configuration.
    """
    headers = await _admin(api, workspace)
    created = await api.post(
        workspace.path("/settings/stages"),
        headers=headers,
        json={"label": "Hot", "color": "#ef4444", "kind": "ACTIVE"},
    )
    assert created.status_code == 201, created.text
    hot_id = created.json()["id"]

    stages = (await api.get(workspace.path("/settings/stages"), headers=headers)).json()
    lost_id = stages["lost"]["id"]
    reasons = (await api.get(workspace.path("/settings/lost-reasons"), headers=headers)).json()

    transitioned = await _lead(api, workspace, headers, name="Went cold", phone="9320000001")
    stayed_hot = await _lead(api, workspace, headers, name="Still hot", phone="9320000002")

    for lead_id in (transitioned["id"], stayed_hot["id"]):
        moved = await api.patch(
            workspace.path(f"/leads/{lead_id}"), headers=headers, json={"stage_id": hot_id}
        )
        assert moved.status_code == 200, moved.text

    lost = await api.patch(
        workspace.path(f"/leads/{transitioned['id']}"),
        headers=headers,
        json={"stage_id": lost_id, "lost_reason_id": reasons[0]["id"]},
    )
    assert lost.status_code == 200, lost.text

    page = await _search(
        api,
        workspace,
        headers,
        filter={
            "type": "status_changed",
            "from_stage_id": hot_id,
            "to_stage_id": lost_id,
            "within": {"last_days": 7},
        },
    )
    assert _ids(page) == {transitioned["id"]}


async def test_action_performed_counts_with_min_count(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    busy = await _lead(api, workspace, headers, name="Busy", phone="9330000001")
    quiet = await _lead(api, workspace, headers, name="Quiet", phone="9330000002")

    for _ in range(3):
        await _log_call(api, workspace, headers, busy["id"])
    await _log_call(api, workspace, headers, quiet["id"])

    page = await _search(
        api,
        workspace,
        headers,
        filter={"type": "action_performed", "action_kind": "CALL_LOGGED", "min_count": 3},
    )
    assert _ids(page) == {busy["id"]}


async def test_assignee_changed_matches_on_either_end(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    rep = workspace.members["rep"]

    moved = await _lead(api, workspace, headers, name="Moved", phone="9340000001")
    await _lead(api, workspace, headers, name="Untouched", phone="9340000002")

    assigned = await api.patch(
        workspace.path(f"/leads/{moved['id']}"),
        headers=headers,
        json={"assignee_id": str(rep.membership.id)},
    )
    assert assigned.status_code == 200, assigned.text

    page = await _search(
        api,
        workspace,
        headers,
        filter={
            "type": "assignee_changed",
            "to_membership_id": str(rep.membership.id),
            "within": {"last_days": 7},
        },
    )
    assert _ids(page) == {moved["id"]}


async def test_a_history_predicate_combines_with_a_field_rule(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The shape the audit said the old DSL could not express at all."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")

    target = await _lead(api, workspace, headers, name="A", phone="9350000001", city="Chennai")
    await _lead(api, workspace, headers, name="B", phone="9350000002", city="Chennai")
    await _lead(api, workspace, headers, name="C", phone="9350000003", city="Delhi")

    await _log_call(api, workspace, headers, target["id"])

    page = await _search(
        api,
        workspace,
        headers,
        filter={
            "type": "group",
            "op": "AND",
            "children": [
                {"type": "field", "key": "city", "op": "eq", "value": "Chennai"},
                {"type": "action_performed", "action_kind": "CALL_LOGGED"},
            ],
        },
    )
    assert _ids(page) == {target["id"]}


# --- search ------------------------------------------------------------------


async def test_search_matches_a_workspace_defined_field_through_the_vector(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Company", field_type="TEXT")

    match = await _lead(
        api, workspace, headers, name="Someone", phone="9360000001", company="Northwind"
    )
    await _lead(api, workspace, headers, name="Another", phone="9360000002", company="Acme")

    page = (
        await api.get(workspace.path("/leads"), headers=headers, params={"q": "Northwind"})
    ).json()
    assert _ids(page) == {match["id"]}


async def test_search_matches_a_fragment_of_the_identity_value(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The trigram half. A tsquery matches whole lexemes, so a phone fragment
    would find nothing without it — and typing the last digits of a number is
    how people actually search a CRM."""
    headers = await _admin(api, workspace)
    match = await _lead(api, workspace, headers, name="Fragment", phone="9370012345")
    await _lead(api, workspace, headers, name="Other", phone="9370098765")

    page = (await api.get(workspace.path("/leads"), headers=headers, params={"q": "12345"})).json()
    assert _ids(page) == {match["id"]}


async def test_the_search_vector_follows_an_edit(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """A vector that lagged its values would make a lead invisible to search,
    which reads to the user as "the lead is gone"."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Company", field_type="TEXT")
    lead = await _lead(
        api, workspace, headers, name="Renamer", phone="9380000001", company="Oldname"
    )

    updated = await api.patch(
        workspace.path(f"/leads/{lead['id']}"),
        headers=headers,
        json={"values": {"company": "Newname"}},
    )
    assert updated.status_code == 200, updated.text

    found = (
        await api.get(workspace.path("/leads"), headers=headers, params={"q": "Newname"})
    ).json()
    assert _ids(found) == {lead["id"]}
    stale = (
        await api.get(workspace.path("/leads"), headers=headers, params={"q": "Oldname"})
    ).json()
    assert stale["total"] == 0


# --- sorting -----------------------------------------------------------------


async def test_sorting_by_a_builtin_column_works(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    first = await _lead(api, workspace, headers, name="First", phone="9390000001")
    second = await _lead(api, workspace, headers, name="Second", phone="9390000002")

    page = (
        await api.get(workspace.path("/leads"), headers=headers, params={"sort": "created_at"})
    ).json()
    assert [i["id"] for i in page["items"]] == [first["id"], second["id"]]

    reverse = (
        await api.get(workspace.path("/leads"), headers=headers, params={"sort": "-created_at"})
    ).json()
    assert [i["id"] for i in reverse["items"]] == [second["id"], first["id"]]


async def test_sorting_by_an_unindexed_field_names_the_setting_that_fixes_it(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The contract M6 states: 400 `field_not_indexed`, with the fix in the message."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")

    response = await api.get(workspace.path("/leads"), headers=headers, params={"sort": "city"})
    assert response.status_code == 400, response.text
    body = response.json()["detail"]
    assert body["code"] == "field_not_indexed"
    assert "Index" in body["message"], "the message must name the setting that fixes it"
    assert body["field_key"] == "city"


async def test_sorting_by_an_indexed_field_works(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    field = await _field(api, workspace, headers, label="City", field_type="TEXT")

    declared = await api.post(
        workspace.path("/settings/indexed-fields"),
        headers=headers,
        json={"field_id": field["id"]},
    )
    assert declared.status_code == 202, declared.text

    zulu = await _lead(api, workspace, headers, name="Z", phone="9400000001", city="Zulu")
    alpha = await _lead(api, workspace, headers, name="A", phone="9400000002", city="Alpha")

    page = (
        await api.get(workspace.path("/leads"), headers=headers, params={"sort": "city"})
    ).json()
    assert [i["id"] for i in page["items"]] == [alpha["id"], zulu["id"]]


async def test_sorting_by_an_unknown_field_is_refused(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    response = await api.get(
        workspace.path("/leads"), headers=headers, params={"sort": "no_such_field"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unknown_sort_field"


# --- column hydration --------------------------------------------------------


async def test_columns_narrow_the_hydrated_values(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")
    await _lead(api, workspace, headers, name="Someone", phone="9410000001", city="Chennai")

    page = await _search(api, workspace, headers, filter=_EMPTY, columns=["city"])
    values = page["items"][0]["values"]
    assert set(values) == {"city"}, "only the requested column should be hydrated"


async def test_columns_cannot_widen_past_the_view_grant(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Narrowing is a convenience; it must never be a way to ask for more."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Salary", field_type="NUMBER")
    await _lead(api, workspace, headers, name="Earner", phone="9420000001", salary=1)

    rep = workspace.members["rep"]
    await login(api, rep)
    page = await _search(api, workspace, rep.auth, filter=_EMPTY, columns=["salary"])
    assert page["items"][0]["values"] == {}


# --- saved filters -----------------------------------------------------------


async def _save(
    api: AsyncClient, ws: WorkspaceFixture, headers: dict[str, str], **payload: Any
) -> dict:
    body = {"definition": _EMPTY, **payload}
    response = await api.post(ws.path("/filters"), headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_personal_filter_is_invisible_to_other_members(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    mine = await _save(api, workspace, headers, name="Mine", visibility="PERSONAL")

    rep = workspace.members["rep"]
    await login(api, rep)
    listed = (await api.get(workspace.path("/filters"), headers=rep.auth)).json()
    assert mine["id"] not in {f["id"] for f in listed}


async def test_a_shared_filter_is_visible_to_everyone(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    shared = await _save(api, workspace, headers, name="Team worklist", visibility="SHARED")

    rep = workspace.members["rep"]
    await login(api, rep)
    listed = (await api.get(workspace.path("/filters"), headers=rep.auth)).json()
    assert shared["id"] in {f["id"] for f in listed}


async def test_a_role_filter_reaches_only_its_template(
    api: AsyncClient, workspace: WorkspaceFixture, db_session: AsyncSession
) -> None:
    headers = await _admin(api, workspace)
    templates = (
        await api.get(workspace.path("/settings/permission-templates"), headers=headers)
    ).json()
    caller = next(t for t in templates if t["name"] == "Caller")
    marketing = next(t for t in templates if t["name"] == "Marketing")

    for_callers = await _save(
        api,
        workspace,
        headers,
        name="Caller worklist",
        visibility="ROLE",
        template_id=caller["id"],
    )
    for_marketing = await _save(
        api,
        workspace,
        headers,
        name="Marketing view",
        visibility="ROLE",
        template_id=marketing["id"],
    )

    rep = workspace.members["rep"]  # on the Caller template
    await login(api, rep)
    visible = {
        f["id"] for f in (await api.get(workspace.path("/filters"), headers=rep.auth)).json()
    }
    assert for_callers["id"] in visible
    assert for_marketing["id"] not in visible


async def test_a_role_filter_must_name_a_template(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    response = await api.post(
        workspace.path("/filters"),
        headers=headers,
        json={"name": "Roleless", "definition": _EMPTY, "visibility": "ROLE"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "template_required"


async def test_sharing_a_filter_does_not_share_a_hidden_column(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The property the whole visibility model rests on.

    A shared filter is a shared *question*. Running someone else's filter still
    projects through the runner's own grants, so it can never become a way to
    read a column they were not granted.
    """
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="Salary", field_type="NUMBER")
    await _lead(api, workspace, headers, name="Earner", phone="9430000001", salary=99)
    shared = await _save(api, workspace, headers, name="Everyone", visibility="SHARED")

    rep = workspace.members["rep"]
    await login(api, rep)
    stats = await api.get(workspace.path(f"/filters/{shared['id']}/stats"), headers=rep.auth)
    assert stats.status_code == 200, stats.text

    page = await _search(api, workspace, rep.auth, filter=_EMPTY)
    assert all("salary" not in item["values"] for item in page["items"])


async def test_only_the_owner_or_an_admin_can_edit_a_filter(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    shared = await _save(api, workspace, headers, name="Owned by admin", visibility="SHARED")

    rep = workspace.members["rep"]
    await login(api, rep)
    response = await api.patch(
        workspace.path(f"/filters/{shared['id']}"), headers=rep.auth, json={"name": "Hijacked"}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "not_filter_owner"


async def test_duplicating_a_shared_filter_makes_a_personal_copy(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Copying to tweak must not publish the tweak."""
    headers = await _admin(api, workspace)
    shared = await _save(api, workspace, headers, name="Team", visibility="SHARED")

    rep = workspace.members["rep"]
    await login(api, rep)
    copy = await api.post(workspace.path(f"/filters/{shared['id']}/duplicate"), headers=rep.auth)
    assert copy.status_code == 201, copy.text
    assert copy.json()["visibility"] == "PERSONAL"
    assert copy.json()["owner_membership_id"] == str(rep.membership.id)


async def test_filter_stats_count_through_the_callers_own_visibility(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    await _lead(api, workspace, headers, name="One", phone="9440000001")
    await _lead(api, workspace, headers, name="Two", phone="9440000002")
    saved = await _save(api, workspace, headers, name="All", visibility="SHARED")

    stats = (await api.get(workspace.path(f"/filters/{saved['id']}/stats"), headers=headers)).json()
    assert stats["total"] == 2
    assert sum(stats["by_stage"].values()) == 2


async def test_filters_can_be_reordered(api: AsyncClient, workspace: WorkspaceFixture) -> None:
    headers = await _admin(api, workspace)
    first = await _save(api, workspace, headers, name="First", visibility="SHARED")
    second = await _save(api, workspace, headers, name="Second", visibility="SHARED")

    reordered = await api.patch(
        workspace.path("/filters/reorder"),
        headers=headers,
        json={"filter_ids": [second["id"], first["id"]]},
    )
    assert reordered.status_code == 200, reordered.text
    assert [f["id"] for f in reordered.json()] == [second["id"], first["id"]]


async def test_an_archived_filter_leaves_the_list(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    saved = await _save(api, workspace, headers, name="Temporary", visibility="SHARED")

    archived = await api.delete(workspace.path(f"/filters/{saved['id']}"), headers=headers)
    assert archived.status_code == 200, archived.text

    listed = (await api.get(workspace.path("/filters"), headers=headers)).json()
    assert saved["id"] not in {f["id"] for f in listed}


async def test_a_malformed_filter_is_refused_at_save_time(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Rejected on save, not on every later run."""
    response = await api.post(
        workspace.path("/filters"),
        headers=await _admin(api, workspace),
        json={
            "name": "Broken",
            "definition": {"type": "field", "key": "name", "op": "not_a_real_operator"},
        },
    )
    assert response.status_code == 422


# --- table layouts -----------------------------------------------------------


async def test_a_layout_round_trips_per_member_and_filter(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    saved = await _save(api, workspace, headers, name="Worklist", visibility="SHARED")

    written = await api.put(
        workspace.path("/layouts"),
        headers=headers,
        params={"filter_id": saved["id"]},
        json={"columns": ["identity_value", "score"], "sort_key": "score", "sort_desc": True},
    )
    assert written.status_code == 200, written.text

    read = (
        await api.get(
            workspace.path("/layouts"), headers=headers, params={"filter_id": saved["id"]}
        )
    ).json()
    assert read["columns"] == ["identity_value", "score"]

    # The default layout is a different row from a filter's layout.
    default = (await api.get(workspace.path("/layouts"), headers=headers)).json()
    assert default is None


async def test_two_members_keep_separate_layouts(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    await api.put(workspace.path("/layouts"), headers=headers, json={"columns": ["score"]})

    rep = workspace.members["rep"]
    await login(api, rep)
    await api.put(workspace.path("/layouts"), headers=rep.auth, json={"columns": ["rating"]})

    mine = (await api.get(workspace.path("/layouts"), headers=headers)).json()
    theirs = (await api.get(workspace.path("/layouts"), headers=rep.auth)).json()
    assert mine["columns"] == ["score"]
    assert theirs["columns"] == ["rating"]


async def test_saving_a_layout_twice_updates_rather_than_duplicating(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """A layout is a preference, so a second save is not a conflict — and the
    partial unique index would reject a second default row anyway."""
    headers = await _admin(api, workspace)
    await api.put(workspace.path("/layouts"), headers=headers, json={"columns": ["score"]})
    second = await api.put(
        workspace.path("/layouts"), headers=headers, json={"columns": ["rating"]}
    )
    assert second.status_code == 200, second.text
    assert second.json()["columns"] == ["rating"]


# --- quick filters -----------------------------------------------------------
#
# Stage and assignee are columns on `leads`, not workspace-defined fields, so
# §6.1's field rule cannot reach them — a rule references `lead_fields.key` by
# definition. They are also the two filters every CRM user reaches for first,
# which is why the API contract gives the list endpoint quick filters *beside*
# the DSL rather than folding them into it.


async def test_quick_filter_by_stage(api: AsyncClient, workspace: WorkspaceFixture) -> None:
    headers = await _admin(api, workspace)
    stages = await _stages(api, workspace, headers)

    won = await _lead(api, workspace, headers, name="Won", phone="9500000001")
    await _lead(api, workspace, headers, name="Open", phone="9500000002")
    moved = await api.patch(
        workspace.path(f"/leads/{won['id']}"), headers=headers, json={"stage_id": stages["won"]}
    )
    assert moved.status_code == 200, moved.text

    page = (
        await api.get(workspace.path("/leads"), headers=headers, params={"stage_id": stages["won"]})
    ).json()
    assert _ids(page) == {won["id"]}


async def test_quick_filter_by_stage_kind_includes_stageless_leads(
    api: AsyncClient, workspace: WorkspaceFixture, db_session: AsyncSession
) -> None:
    """ "Still open" must include a lead whose stage was deleted.

    The same NULL trap the deactivation check has to sidestep: `stage_id NOT IN
    (closed)` is NULL for a stageless lead, which is not TRUE, so it would drop
    exactly the rows that most need chasing.
    """
    headers = await _admin(api, workspace)
    stages = await _stages(api, workspace, headers)

    open_lead = await _lead(api, workspace, headers, name="Open", phone="9510000001")
    stageless = await _lead(api, workspace, headers, name="Stageless", phone="9510000002")
    won = await _lead(api, workspace, headers, name="Won", phone="9510000003")
    await api.patch(
        workspace.path(f"/leads/{won['id']}"), headers=headers, json={"stage_id": stages["won"]}
    )
    await db_session.execute(
        update(Lead).where(Lead.id == uuid.UUID(stageless["id"])).values(stage_id=None)
    )
    await db_session.commit()

    page = (
        await api.get(
            workspace.path("/leads"),
            headers=headers,
            params=[("stage_kinds", "INITIAL"), ("stage_kinds", "ACTIVE")],
        )
    ).json()
    assert _ids(page) == {open_lead["id"], stageless["id"]}


async def test_quick_filter_by_assignee_and_unassigned(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    rep = workspace.members["rep"]

    assigned = await _lead(api, workspace, headers, name="Assigned", phone="9520000001")
    unassigned = await _lead(api, workspace, headers, name="Nobody", phone="9520000002")
    await api.patch(
        workspace.path(f"/leads/{assigned['id']}"),
        headers=headers,
        json={"assignee_id": str(rep.membership.id)},
    )

    mine = (
        await api.get(
            workspace.path("/leads"),
            headers=headers,
            params={"assignee_id": str(rep.membership.id)},
        )
    ).json()
    assert _ids(mine) == {assigned["id"]}

    # A separate flag, because a query string cannot distinguish "no assignee
    # given" from "explicitly nobody" — and those mean opposite things.
    nobody = (
        await api.get(workspace.path("/leads"), headers=headers, params={"unassigned": "true"})
    ).json()
    assert _ids(nobody) == {unassigned["id"]}


async def test_quick_filters_combine_with_the_dsl(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The two narrow together rather than one replacing the other."""
    headers = await _admin(api, workspace)
    await _field(api, workspace, headers, label="City", field_type="TEXT")
    stages = await _stages(api, workspace, headers)

    target = await _lead(api, workspace, headers, name="A", phone="9530000001", city="Chennai")
    other = await _lead(api, workspace, headers, name="B", phone="9530000002", city="Chennai")
    await _lead(api, workspace, headers, name="C", phone="9530000003", city="Delhi")

    for lead_id in (target["id"], other["id"]):
        await api.patch(
            workspace.path(f"/leads/{lead_id}"),
            headers=headers,
            json={"stage_id": stages["won"] if lead_id == target["id"] else stages["initial"]},
        )

    page = await _search(
        api,
        workspace,
        headers,
        filter={"type": "field", "key": "city", "op": "eq", "value": "Chennai"},
        stage_id=stages["won"],
    )
    assert _ids(page) == {target["id"]}
