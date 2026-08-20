"""The field-definition endpoints (M2).

Covers the settings surface the admin actually drives: creating one field of
every type, the option editor, the composites cascading, and the workspace-level
identity and H1/H2 pickers.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import WorkspaceFixture, add_member, build_workspace, login

from app.auth.passwords import PasswordHasherService
from app.models.enums import LeadFieldType

pytestmark = pytest.mark.integration


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


@pytest.fixture
async def workspace(
    db_session: AsyncSession,
    hasher: PasswordHasherService,
) -> WorkspaceFixture:
    """A provisioned workspace with an admin owner and one non-admin rep.

    The rep exists so every settings endpoint can be checked for the
    admin-only rule without a second fixture.
    """
    fixture = await build_workspace(
        db_session, hasher, name="Acme", owner_email="owner@example.com"
    )
    await add_member(
        db_session,
        hasher,
        fixture,
        key="rep",
        email="rep@example.com",
        template_name="Caller",
    )
    # Everything above is committed, but the reads that follow each commit have
    # left this session holding an open transaction. Declaring an indexed field
    # runs `CREATE INDEX CONCURRENTLY`, which waits for every transaction older
    # than itself to finish — so an idle-in-transaction fixture does not fail
    # the test, it hangs it. Let go of the transaction here rather than leaving
    # that trap for the next person to add an index test.
    #
    # `commit`, not `rollback`: the factory hands back live ORM objects and
    # rollback expires every one of them, `expire_on_commit=False`
    # notwithstanding — the next attribute read would then emit IO from a
    # non-async context and fail as MissingGreenlet.
    await db_session.commit()
    return fixture


async def _admin(api: AsyncClient, workspace: WorkspaceFixture) -> dict[str, str]:
    await login(api, workspace.owner)
    return workspace.owner.auth


async def _create_field(
    api: AsyncClient,
    workspace: WorkspaceFixture,
    headers: dict[str, str],
    **payload: object,
) -> dict:
    response = await api.post(
        workspace.path("/settings/lead-fields"), headers=headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- provisioning ------------------------------------------------------------


async def test_a_new_workspace_has_exactly_the_four_builtin_fields(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """docs/01-data-model.md §7 — and nothing industry-specific."""
    headers = await _admin(api, workspace)
    response = await api.get(workspace.path("/settings/lead-fields"), headers=headers)
    assert response.status_code == 200

    fields = response.json()
    assert [f["label"] for f in fields] == ["Name", "Phone", "Email", "Alternate Phone"]
    assert all(f["is_builtin"] for f in fields)
    assert [f["field_type"] for f in fields] == ["TEXT", "PHONE", "EMAIL", "PHONE"]


async def test_provisioning_points_identity_and_primary_fields_at_the_builtins(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    detail = await api.get(workspace.path(), headers=headers)
    body = detail.json()
    fields = {
        f["key"]: f["id"]
        for f in (await api.get(workspace.path("/settings/lead-fields"), headers=headers)).json()
    }
    assert body["identity_field_id"] == fields["phone"]
    assert body["primary_field_1_id"] == fields["name"]


# --- the registry endpoints --------------------------------------------------


async def test_the_registry_endpoint_serves_all_thirteen_types(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The frontend reads this instead of hardcoding a list."""
    headers = await _admin(api, workspace)
    response = await api.get(workspace.path("/settings/field-types"), headers=headers)
    assert response.status_code == 200

    types = response.json()
    assert len(types) == 13
    assert {t["key"] for t in types} == {t.value for t in LeadFieldType}
    # Each entry carries what a renderer needs.
    assert all(t["renderer"]["widget"] for t in types)
    assert all(t["operators"] for t in types)


async def test_the_action_registry_endpoint_serves_all_eight_types(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    response = await api.get(workspace.path("/settings/action-field-types"), headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 8


# --- creating one field of every type ----------------------------------------


async def test_an_admin_can_create_one_field_of_every_type(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The M2 acceptance check, driven through the API the UI uses."""
    headers = await _admin(api, workspace)

    for field_type in LeadFieldType:
        created = await _create_field(
            api,
            workspace,
            headers,
            label=f"Test {field_type.value.title()}",
            field_type=field_type.value,
        )
        assert created["field_type"] == field_type.value
        assert created["key"], "every field gets a derived key"

    listed = await api.get(workspace.path("/settings/lead-fields"), headers=headers)
    # 4 built-ins plus one of each of the 13.
    assert len(listed.json()) == 4 + 13


async def test_the_derived_key_is_stable_when_the_label_changes(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """§3.1: renaming must not orphan the values stored under the key."""
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Budget Range", field_type="TEXT")
    assert field["key"] == "budget_range"

    renamed = await api.patch(
        workspace.path(f"/settings/lead-fields/{field['id']}"),
        headers=headers,
        json={"label": "Deal Size"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["label"] == "Deal Size"
    assert renamed.json()["key"] == "budget_range", "the key is immutable"


async def test_duplicate_labels_get_distinct_keys(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    first = await _create_field(api, workspace, headers, label="Region", field_type="TEXT")
    second = await _create_field(api, workspace, headers, label="Region", field_type="TEXT")
    assert first["key"] != second["key"]


async def test_a_field_type_cannot_be_changed_after_creation(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Not offered by the schema — every stored value would be invalidated."""
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Score", field_type="NUMBER")
    response = await api.patch(
        workspace.path(f"/settings/lead-fields/{field['id']}"),
        headers=headers,
        json={"field_type": "TEXT"},
    )
    # Unknown keys are ignored by the update model rather than applied.
    assert response.status_code == 200
    assert response.json()["field_type"] == "NUMBER"


# --- hiding ------------------------------------------------------------------


async def test_fields_hide_rather_than_delete(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Legacy", field_type="TEXT")

    hidden = await api.post(
        workspace.path(f"/settings/lead-fields/{field['id']}/hide"), headers=headers
    )
    assert hidden.status_code == 200
    assert hidden.json()["is_hidden"] is True

    default_list = await api.get(workspace.path("/settings/lead-fields"), headers=headers)
    assert field["id"] not in {f["id"] for f in default_list.json()}

    with_hidden = await api.get(
        workspace.path("/settings/lead-fields"),
        headers=headers,
        params={"include_hidden": True},
    )
    assert field["id"] in {f["id"] for f in with_hidden.json()}


async def test_a_builtin_field_cannot_be_hidden(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    fields = (await api.get(workspace.path("/settings/lead-fields"), headers=headers)).json()
    name = next(f for f in fields if f["key"] == "name")

    response = await api.post(
        workspace.path(f"/settings/lead-fields/{name['id']}/hide"), headers=headers
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "builtin_field"


async def test_the_identity_field_cannot_be_hidden(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Hiding it would cost the workspace its ability to dedupe."""
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Ref", field_type="TEXT")
    await api.put(
        workspace.path("/settings/identity-field"),
        headers=headers,
        json={"field_id": field["id"]},
    )
    response = await api.post(
        workspace.path(f"/settings/lead-fields/{field['id']}/hide"), headers=headers
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "identity_field_in_use"


# --- options -----------------------------------------------------------------


async def test_the_option_editor_supports_add_bulk_and_reorder(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Tier", field_type="DROPDOWN")
    base = workspace.path(f"/settings/lead-fields/{field['id']}/options")

    one = await api.post(base, headers=headers, json={"label": "First", "color": "#ff0000"})
    assert one.status_code == 201
    assert one.json()["color"] == "#ff0000"

    bulk = await api.post(
        f"{base}/bulk", headers=headers, json={"labels": ["Second", "Third", "", "Second"]}
    )
    assert bulk.status_code == 201
    # Blanks and duplicates skipped — pasting a spreadsheet column includes both.
    assert [o["label"] for o in bulk.json()] == ["Second", "Third"]

    options = (await api.get(base, headers=headers)).json()
    reordered = await api.patch(
        f"{base}/reorder",
        headers=headers,
        json={"ordered_ids": [o["id"] for o in reversed(options)]},
    )
    assert reordered.status_code == 200
    assert [o["label"] for o in reordered.json()] == ["Third", "Second", "First"]


async def test_options_archive_rather_than_delete(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """§2.3: deletion removes it from the picker, not from history."""
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Grade", field_type="DROPDOWN")
    base = workspace.path(f"/settings/lead-fields/{field['id']}/options")
    option = (await api.post(base, headers=headers, json={"label": "Retired"})).json()

    deleted = await api.delete(f"{base}/{option['id']}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["is_archived"] is True

    # Still present, still readable — the row survives for history.
    assert option["id"] in {o["id"] for o in (await api.get(base, headers=headers)).json()}


async def test_copy_options_clones_another_fields_set(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    source = await _create_field(api, workspace, headers, label="Source", field_type="DROPDOWN")
    target = await _create_field(api, workspace, headers, label="Target", field_type="TAGS")

    await api.post(
        workspace.path(f"/settings/lead-fields/{source['id']}/options/bulk"),
        headers=headers,
        json={"labels": ["Alpha", "Beta"]},
    )
    copied = await api.post(
        workspace.path(f"/settings/lead-fields/{target['id']}/options/copy-from/{source['id']}"),
        headers=headers,
    )
    assert copied.status_code == 201
    assert {o["label"] for o in copied.json()} == {"Alpha", "Beta"}


async def test_a_type_without_options_refuses_them(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Notes", field_type="TEXT")
    response = await api.post(
        workspace.path(f"/settings/lead-fields/{field['id']}/options"),
        headers=headers,
        json={"label": "nope"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "type_has_no_options"


async def test_a_dependent_dropdown_builds_a_cascade(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """The M2 acceptance check for the composite: a child names its parent."""
    headers = await _admin(api, workspace)
    field = await _create_field(
        api, workspace, headers, label="Territory", field_type="DEPENDENT_DROPDOWN"
    )
    base = workspace.path(f"/settings/lead-fields/{field['id']}/options")

    parent = (await api.post(base, headers=headers, json={"label": "Parent Region"})).json()
    child = (
        await api.post(
            base,
            headers=headers,
            json={"label": "Child Area", "parent_option_id": parent["id"]},
        )
    ).json()

    assert child["parent_option_id"] == parent["id"]
    assert parent["parent_option_id"] is None

    tree = (await api.get(base, headers=headers)).json()
    assert {o["id"]: o["parent_option_id"] for o in tree} == {
        parent["id"]: None,
        child["id"]: parent["id"],
    }


async def test_only_a_dependent_dropdown_accepts_a_parent_option(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    plain = await _create_field(api, workspace, headers, label="Flat", field_type="DROPDOWN")
    base = workspace.path(f"/settings/lead-fields/{plain['id']}/options")
    first = (await api.post(base, headers=headers, json={"label": "One"})).json()

    response = await api.post(
        base, headers=headers, json={"label": "Two", "parent_option_id": first["id"]}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "not_a_cascade"


# --- identity and primary fields ---------------------------------------------


async def test_the_identity_field_is_configurable(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """§1.1: "A B2B customer might key on Email; a dealership on registration
    number." It is a setting, not a constant."""
    headers = await _admin(api, workspace)
    fields = {
        f["key"]: f["id"]
        for f in (await api.get(workspace.path("/settings/lead-fields"), headers=headers)).json()
    }

    response = await api.put(
        workspace.path("/settings/identity-field"),
        headers=headers,
        json={"field_id": fields["email"]},
    )
    assert response.status_code == 200
    assert response.json()["identity_field_id"] == fields["email"]


async def test_an_unsuitable_type_cannot_be_the_identity_field(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    tags = await _create_field(api, workspace, headers, label="Labels", field_type="TAGS")
    response = await api.put(
        workspace.path("/settings/identity-field"),
        headers=headers,
        json={"field_id": tags["id"]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsuitable_identity_field"


async def test_primary_fields_drive_the_headline(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    fields = {
        f["key"]: f["id"]
        for f in (await api.get(workspace.path("/settings/lead-fields"), headers=headers)).json()
    }
    response = await api.put(
        workspace.path("/settings/primary-fields"),
        headers=headers,
        json={"h1_field_id": fields["email"], "h2_field_id": fields["name"]},
    )
    assert response.status_code == 200
    assert response.json()["h1_field_id"] == fields["email"]


# --- indexed fields ----------------------------------------------------------


async def test_declaring_an_indexed_field_returns_pending(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """202 with PENDING: the row exists, the index does not yet.

    `CREATE INDEX CONCURRENTLY` cannot run in the request's transaction.
    """
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Sortable", field_type="TEXT")

    response = await api.post(
        workspace.path("/settings/indexed-fields"),
        headers=headers,
        json={"field_id": field["id"]},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PENDING"
    # Generated from ids, never from the customer's label, and inside the
    # 63-byte identifier limit.
    assert body["index_name"].startswith("ix_lv_")
    assert len(body["index_name"]) <= 63
    assert "sortable" not in body["index_name"]


async def test_the_eight_index_limit_is_enforced(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """§2.4 — at most 8 per workspace."""
    headers = await _admin(api, workspace)
    for n in range(9):
        field = await _create_field(
            api, workspace, headers, label=f"Indexed {n}", field_type="TEXT"
        )
        response = await api.post(
            workspace.path("/settings/indexed-fields"),
            headers=headers,
            json={"field_id": field["id"]},
        )
        if n < 8:
            assert response.status_code == 202
        else:
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "indexed_field_limit"


async def test_a_field_cannot_be_indexed_twice(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Once", field_type="TEXT")
    body = {"field_id": field["id"]}
    assert (
        await api.post(workspace.path("/settings/indexed-fields"), headers=headers, json=body)
    ).status_code == 202
    repeat = await api.post(workspace.path("/settings/indexed-fields"), headers=headers, json=body)
    assert repeat.status_code == 409
    assert repeat.json()["detail"]["code"] == "already_indexed"


# --- permissions -------------------------------------------------------------


async def test_a_rep_cannot_change_the_schema(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Field definitions are admin-only; reading them is not."""
    rep = workspace.members["rep"]
    await login(api, rep)

    readable = await api.get(workspace.path("/settings/lead-fields"), headers=rep.auth)
    assert readable.status_code == 200

    forbidden = await api.post(
        workspace.path("/settings/lead-fields"),
        headers=rep.auth,
        json={"label": "Sneaky", "field_type": "TEXT"},
    )
    assert forbidden.status_code == 403


# --- the indexed-field worker ------------------------------------------------
#
# The three tests above assert the declaration contract: a row appears, the
# limit holds, the name is generated. None of them says whether an index is
# ever built — and between M5 and this milestone none was. `declare_indexed`
# wrote PENDING and nothing invoked the worker, so the status never moved and
# M6's "sorting is restricted to indexed fields" contract described an index
# that did not exist.


async def _index_validity(db_session: AsyncSession, name: str) -> bool | None:
    """None when no such index exists, else whether Postgres considers it valid.

    Committing before returning is not tidiness: `CREATE INDEX CONCURRENTLY`
    and `DROP INDEX CONCURRENTLY` both wait for every transaction older than
    themselves, so a lingering read snapshot here would stall the next one.
    """
    rows = await db_session.execute(
        text(
            "select i.indisvalid from pg_class c "
            "join pg_index i on i.indexrelid = c.oid where c.relname = :name"
        ),
        {"name": name},
    )
    validity: bool | None = rows.scalar_one_or_none()
    await db_session.commit()
    return validity


async def test_declaring_a_field_actually_builds_the_index(
    api: AsyncClient, workspace: WorkspaceFixture, db_session: AsyncSession
) -> None:
    """PENDING has to become READY, against a real index in the catalogue."""
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Sortable", field_type="TEXT")

    declared = await api.post(
        workspace.path("/settings/indexed-fields"),
        headers=headers,
        json={"field_id": field["id"]},
    )
    assert declared.status_code == 202
    name = declared.json()["index_name"]
    # The declaration is still PENDING in the response — the build runs after
    # it, which is the whole reason this endpoint answers 202.
    assert declared.json()["status"] == "PENDING"

    listed = await api.get(workspace.path("/settings/indexed-fields"), headers=headers)
    entry = next(i for i in listed.json() if i["field_id"] == field["id"])
    assert entry["status"] == "READY", entry["last_error"]
    assert entry["last_error"] is None

    assert await _index_validity(db_session, name) is True


async def test_undeclaring_a_field_drops_the_index(
    api: AsyncClient, workspace: WorkspaceFixture, db_session: AsyncSession
) -> None:
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Temporary", field_type="TEXT")

    declared = await api.post(
        workspace.path("/settings/indexed-fields"),
        headers=headers,
        json={"field_id": field["id"]},
    )
    name = declared.json()["index_name"]
    assert await _index_validity(db_session, name) is True

    dropped = await api.delete(
        workspace.path(f"/settings/indexed-fields/{field['id']}"),
        headers=headers,
    )
    assert dropped.status_code == 202
    assert dropped.json()["index_name"] == name

    assert await _index_validity(db_session, name) is None

    remaining = await api.get(workspace.path("/settings/indexed-fields"), headers=headers)
    assert [i for i in remaining.json() if i["field_id"] == field["id"]] == []


async def test_a_field_can_be_indexed_again_after_being_undeclared(
    api: AsyncClient, workspace: WorkspaceFixture, db_session: AsyncSession
) -> None:
    """Re-declaring must rebuild, not silently inherit the previous index.

    `CREATE INDEX ... IF NOT EXISTS` makes this worth pinning down: if a stale
    index of the same name survived the drop, the rebuild would skip and report
    READY over whatever was already there.
    """
    headers = await _admin(api, workspace)
    field = await _create_field(api, workspace, headers, label="Recurring", field_type="TEXT")
    body = {"field_id": field["id"]}

    first = await api.post(workspace.path("/settings/indexed-fields"), headers=headers, json=body)
    name = first.json()["index_name"]
    await api.delete(workspace.path(f"/settings/indexed-fields/{field['id']}"), headers=headers)
    assert await _index_validity(db_session, name) is None

    again = await api.post(workspace.path("/settings/indexed-fields"), headers=headers, json=body)
    assert again.status_code == 202
    # Deterministic from the two ids, so the rebuilt index takes the same name.
    assert again.json()["index_name"] == name
    assert await _index_validity(db_session, name) is True

    listed = await api.get(workspace.path("/settings/indexed-fields"), headers=headers)
    entry = next(i for i in listed.json() if i["field_id"] == field["id"])
    assert entry["status"] == "READY"
