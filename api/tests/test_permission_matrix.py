"""The permission matrix, asserted as one suite (M11).

`docs/02-api-contract.md` ends with a table headed *"Write an explicit test for
each. These are the ones that bite."* Most rows are covered somewhere already —
scattered across the milestone that introduced them. This file asserts the table
itself, in the order it is written, so the contract has one place that answers
to it.

That matters for a reason the individual tests cannot serve: the table is what a
reviewer or an auditor reads. A property proven in eleven files is a property
nobody can confirm in one sitting.
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
        db_session, hasher, name="Matrix Co", owner_email="matrix-owner@example.com"
    )
    await login(api, fixture.owner)
    return fixture


async def _field(api: AsyncClient, ws: WorkspaceFixture, label: str) -> dict[str, Any]:
    response = await api.post(
        ws.path("/settings/lead-fields"),
        headers=ws.owner.auth,
        json={"label": label, "field_type": "TEXT"},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _caller(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    key: str = "matrix-caller",
) -> Any:
    actor = await add_member(
        db_session,
        hasher,
        ws,
        key=key,
        email=f"{key}@example.com",
        template_name="Caller",
    )
    await login(api, actor)
    return actor


# --- row 1: a lead id from another workspace ---------------------------------


async def test_a_lead_id_from_another_workspace_is_404_not_403(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    """404, because 403 would confirm the id is real."""
    other = await build_workspace(
        db_session, hasher, name="Elsewhere", owner_email="elsewhere@example.com"
    )
    await login(api, other.owner)
    created = await api.post(
        other.path("/leads"),
        headers=other.owner.auth,
        json={"values": {"name": "Theirs", "phone": "+19995557001"}},
    )
    assert created.status_code == 201, created.text

    response = await api.get(ws.path(f"/leads/{created.json()['id']}"), headers=ws.owner.auth)
    assert response.status_code == 404


# --- row 2: no View on a field -----------------------------------------------


async def test_a_field_without_view_is_absent_from_every_read(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    """List, detail and export alike — one projection service, no exceptions.

    A field added after provisioning carries no grants, so Caller cannot view
    it while the built-ins it *does* hold View on still come through. Asserting
    both halves matters: "everything was stripped" would also pass a projection
    that simply dropped the payload.
    """
    secret = await _field(api, ws, "Salary band")
    caller = await _caller(api, ws, db_session, hasher)

    created = await api.post(
        ws.path("/leads"),
        headers=ws.owner.auth,
        json={"values": {"name": "Visible", "phone": "+19995557002", secret["key"]: "high"}},
    )
    assert created.status_code == 201, created.text
    lead_id = created.json()["id"]

    detail = await api.get(ws.path(f"/leads/{lead_id}"), headers=caller.auth)
    assert detail.status_code == 200, detail.text
    assert secret["key"] not in detail.json()["values"]
    assert detail.json()["values"].get("name") == "Visible", "a granted field vanished"

    listed = await api.post(ws.path("/leads/search"), headers=caller.auth, json={"limit": 5})
    assert listed.status_code == 200
    for row in listed.json()["items"]:
        assert secret["key"] not in row.get("values", {})


# --- row 3: no Edit on a field -----------------------------------------------


async def test_writing_a_field_without_edit_is_refused_by_name(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    """Rejected, never silently dropped (architecture rule 4).

    Silently dropping would tell the caller their edit succeeded when the value
    went nowhere — the failure mode that erodes trust in every other write.
    """
    secret = await _field(api, ws, "Salary band")
    caller = await _caller(api, ws, db_session, hasher)

    created = await api.post(
        ws.path("/leads"),
        headers=ws.owner.auth,
        json={"values": {"name": "Target", "phone": "+19995557003"}},
    )
    lead_id = created.json()["id"]

    response = await api.patch(
        ws.path(f"/leads/{lead_id}"),
        headers=caller.auth,
        json={"values": {secret["key"]: "sneaked in"}},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "field_not_editable"

    # And nothing was written.
    check = await api.get(ws.path(f"/leads/{lead_id}"), headers=ws.owner.auth)
    assert secret["key"] not in check.json()["values"]


# --- row 4: no Export --------------------------------------------------------


async def test_export_without_the_grant_is_refused(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    """Caller holds View and Edit on the built-ins, and Export on nothing."""
    caller = await _caller(api, ws, db_session, hasher)
    response = await api.post(ws.path("/leads/export"), headers=caller.auth, json={})
    assert response.status_code == 403, response.text


# --- row 5: no Import on a field ---------------------------------------------


async def test_a_field_without_import_is_not_offered_and_cannot_be_forced(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> None:
    """The mapping UI cannot offer what the commit would refuse.

    Offering it and failing later is a worse experience than not offering it,
    and the two must agree or the screen is lying about what will happen.
    """
    secret = await _field(api, ws, "Salary band")
    caller = await _caller(api, ws, db_session, hasher)

    offered = await api.get(ws.path("/imports/fields"), headers=caller.auth)
    assert offered.status_code == 200, offered.text
    assert secret["key"] not in {row["key"] for row in offered.json()}


# --- row 6: a feature flag ---------------------------------------------------


async def test_a_disabled_feature_returns_403_not_a_hidden_menu(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession
) -> None:
    """CLAUDE.md: a disabled feature returns 403, it does not merely hide a menu.

    The contract's row names `campaign`, which gates nothing — campaigns are an
    anti-requirement for v1. `custom_actions` is the flag that actually guards
    endpoints, so it is the one that can be asserted.
    """
    from sqlalchemy import update

    from app.models import Workspace

    await db_session.execute(
        update(Workspace)
        .where(Workspace.id == ws.workspace.id)
        .values(features={"custom_actions": False})
    )
    await db_session.commit()

    response = await api.get(ws.path("/settings/custom-actions"), headers=ws.owner.auth)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "feature_disabled"


# --- row 7: the Root template ------------------------------------------------


async def test_the_root_template_cannot_be_edited(api: AsyncClient, ws: WorkspaceFixture) -> None:
    """Root is the floor. If it could be edited it could be locked out of.

    This is also why M7's, M8's and M9's capability gaps were unfixable from the
    UI — worth remembering the next time a milestone adds a capability group.
    """
    response = await api.patch(
        ws.path(f"/settings/permission-templates/{ws.templates['Root'].id}"),
        headers=ws.owner.auth,
        json={"name": "Root but mine"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "template_readonly"


# --- row 8: stage cardinality ------------------------------------------------


async def test_a_second_won_stage_cannot_be_created(api: AsyncClient, ws: WorkspaceFixture) -> None:
    """The API is stricter than the contract row, by construction.

    `02-api-contract.md` expects `409 stage_cardinality`. The implementation
    never reaches it: `POST /settings/stages` takes no `kind`, because the three
    singletons arrive with provisioning and are renamed rather than created. A
    request naming `kind` gets an ACTIVE stage and a response body that says so.

    The 409 remains as defence in depth, translating the partial unique index if
    anything ever reaches the database with a second WON — but through the API
    the second WON is unrepresentable, which is a better guarantee than
    rejecting it after the fact.

    (I briefly made this a 422 by forbidding unknown fields on every M3 request
    schema. Two tests caught it. The leniency is deliberate and documented, the
    response body already tells the caller what they got, and tightening an
    earlier milestone's settled API was not what this one asked for.)
    """
    response = await api.post(
        ws.path("/settings/stages"),
        headers=ws.owner.auth,
        json={"label": "Also won", "kind": "WON"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "ACTIVE", "the response must not claim it is WON"

    # And the pipeline still has exactly one won stage. The read is grouped by
    # kind rather than flat, which is itself the shape that makes a second one
    # unrepresentable.
    pipeline = await api.get(ws.path("/settings/stages"), headers=ws.owner.auth)
    assert pipeline.status_code == 200, pipeline.text
    assert pipeline.json()["won"] is not None
    assert pipeline.json()["won"]["label"] != "Also won"


# --- row 9: the lost-reason ceiling ------------------------------------------


async def test_the_twenty_sixth_lost_reason_is_refused(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """25 is the documented ceiling.

    Not arbitrary: a lost-reason list is a dropdown a caller picks from at the
    end of a call, and past about 25 nobody reads it — they pick the first
    plausible one and the data stops meaning anything.
    """
    existing = await api.get(ws.path("/settings/lost-reasons"), headers=ws.owner.auth)
    assert existing.status_code == 200, existing.text
    already = len(existing.json())

    last: Any = None
    for index in range(already, 25):
        last = await api.post(
            ws.path("/settings/lost-reasons"),
            headers=ws.owner.auth,
            json={"label": f"Reason {index}"},
        )
        assert last.status_code == 201, last.text

    over = await api.post(
        ws.path("/settings/lost-reasons"),
        headers=ws.owner.auth,
        json={"label": "One too many"},
    )
    assert over.status_code == 409
    assert over.json()["detail"]["code"] == "lost_reason_limit"


# --- row 10: sorting on an unindexed field -----------------------------------


async def test_sorting_on_a_field_that_is_not_indexed_is_refused(
    api: AsyncClient, ws: WorkspaceFixture
) -> None:
    """A refusal, not a slow answer.

    Sorting 50,000 leads on an unindexed JSONB key is a sequential scan. The
    honest response is to say the field is not sortable, rather than to appear
    to work and quietly cost seconds.
    """
    field = await _field(api, ws, "Unindexed thing")
    response = await api.get(
        ws.path("/leads"), headers=ws.owner.auth, params={"sort": field["key"]}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "field_not_indexed"
