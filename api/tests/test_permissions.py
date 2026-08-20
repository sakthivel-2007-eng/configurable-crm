"""Field-level permissions (M4).

The four checks PROMPTS.md M4 names as the acceptance criteria — a field granted
View but not Edit must be visible in detail, rejected on PATCH, absent from
export, and absent from webhook payloads — are exercised here at the service
level, and again in `test_leads_api` once M5's endpoints exist to carry them.

Testing the chokepoints directly matters: they are the one implementation the
whole product routes through, so a bug here is a bug everywhere at once.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import WorkspaceFixture, add_member, build_workspace, login

from app.auth.passwords import PasswordHasherService
from app.permissions.capabilities import (
    ACCESS_GROUPS,
    PROPOSED_GROUPS,
    VIEW_GROUPS,
    Capabilities,
)
from app.permissions.projection import FieldGrants, FieldProjectionService, FieldWriteFilter

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


# --- the capability model ----------------------------------------------------


def test_every_capability_defaults_to_denied() -> None:
    """Deny by default, expressed in the type rather than in a comment."""
    blank = Capabilities()
    for group in ACCESS_GROUPS:
        section = getattr(blank, group)
        for name in section.__class__.model_fields:
            assert getattr(section, name) is False, f"{group}.{name} defaults to granted"


def test_all_thirteen_groups_are_declared() -> None:
    """§6.2 — 10 Access groups and 3 View groups, contents spec'd not just named."""
    assert len(ACCESS_GROUPS) == 10
    assert len(VIEW_GROUPS) == 3
    blank = Capabilities()
    for group in ACCESS_GROUPS:
        assert len(getattr(blank, group).__class__.model_fields) > 1, f"{group} has no contents"
    for group in VIEW_GROUPS:
        assert len(getattr(blank.view, group).__class__.model_fields) > 1


def test_only_leads_is_marked_observed() -> None:
    """§8 lists nine Access groups and all three View groups as "Not inspected".

    Their contents are proposed here, and the flag exists so that stays visible
    rather than being mistaken for observation.
    """
    assert "leads" not in PROPOSED_GROUPS
    assert set(ACCESS_GROUPS[1:]) | set(VIEW_GROUPS) == PROPOSED_GROUPS


def test_group_admin_access_grants_everything_in_that_group() -> None:
    """Including capabilities added in a later release — that is the point of
    having it as a flag rather than as "every box ticked"."""
    caps = Capabilities.model_validate({"leads": {"admin_access": True}})
    assert caps.allows("leads", "merge_leads")
    assert caps.allows("leads", "bulk_edit")
    # And it does not leak into a different group.
    assert not caps.allows("reports", "export_reports")


def test_a_malformed_capability_blob_denies_rather_than_raising() -> None:
    """A validation error in a settings blob must not stop someone logging in."""
    for blob in (None, {}, {"leads": "not-a-dict"}, {"unknown_group": {"x": True}}):
        caps = Capabilities.from_stored(blob)  # type: ignore[arg-type]
        assert not caps.allows("leads", "bulk_edit")


def test_an_unknown_capability_is_denied_not_an_error() -> None:
    caps = Capabilities.model_validate({"leads": {"search": True}})
    assert caps.allows("leads", "search")
    assert not caps.allows("leads", "teleport")
    assert not caps.allows("nonexistent_group", "anything")


# --- the projection service --------------------------------------------------


def _grants(**kwargs: object) -> FieldGrants:
    base: dict[str, object] = {
        "view": frozenset(),
        "edit": frozenset(),
        "import_": frozenset(),
        "export": frozenset(),
    }
    base.update(kwargs)
    return FieldGrants(**base)  # type: ignore[arg-type]


def test_projection_strips_fields_without_view() -> None:
    projection = FieldProjectionService(_grants(view=frozenset({"name"})))
    projected = projection.project_values({"name": "Ada", "salary": 90000})

    assert projected == {"name": "Ada"}
    # Absent, not null: a null would confirm the field exists and is empty.
    assert "salary" not in projected


def test_export_is_a_separate_grant_from_view() -> None:
    """A caller may legitimately read a phone number on screen and be barred
    from downloading ten thousand of them."""
    projection = FieldProjectionService(
        _grants(view=frozenset({"name", "phone"}), export=frozenset({"name"}))
    )
    assert projection.project_values({"name": "A", "phone": "+1"}) == {"name": "A", "phone": "+1"}
    assert projection.project_export({"name": "A", "phone": "+1"}) == {"name": "A"}


def test_a_template_granting_no_export_is_refused_outright() -> None:
    """The observed default is `Export (0) None` — a deliberate control, so an
    export job is refused rather than producing an empty file."""
    projection = FieldProjectionService(_grants(view=frozenset({"name"})))
    with pytest.raises(HTTPException) as exc:
        projection.assert_can_export()
    assert exc.value.detail["code"] == "export_not_permitted"  # type: ignore[index]


def test_filtering_is_bound_to_view() -> None:
    """Filtering on a hidden field is a read: `salary > 100000` returning a
    count is an oracle over a field the caller cannot see."""
    projection = FieldProjectionService(_grants(view=frozenset({"name"})))
    assert projection.filterable("name")
    assert not projection.filterable("salary")


def test_an_admin_sees_every_field_including_new_ones() -> None:
    """The matrix is bypassed for admin, so a field created after the template
    was last edited is still visible."""
    projection = FieldProjectionService(
        FieldGrants(
            view=frozenset(),
            edit=frozenset(),
            import_=frozenset(),
            export=frozenset(),
            is_admin=True,
        )
    )
    assert projection.project_values({"anything": 1, "added_later": 2}) == {
        "anything": 1,
        "added_later": 2,
    }


# --- the write filter --------------------------------------------------------


def test_a_non_editable_field_is_rejected_not_dropped() -> None:
    """Silently dropping is worse than erroring: the user believes they saved
    something they did not."""
    write_filter = FieldWriteFilter(
        _grants(view=frozenset({"name", "salary"}), edit=frozenset({"name"}))
    )

    with pytest.raises(HTTPException) as exc:
        write_filter.check({"name": "Ada", "salary": 1}, known_keys=frozenset({"name", "salary"}))

    assert exc.value.status_code == 403
    detail = exc.value.detail
    assert detail["code"] == "field_not_editable"  # type: ignore[index]
    assert detail["fields"] == ["salary"]  # type: ignore[index]


def test_every_offending_field_is_named_at_once() -> None:
    """A form with three forbidden fields should say so once, not three times."""
    write_filter = FieldWriteFilter(_grants(edit=frozenset({"name"})))
    with pytest.raises(HTTPException) as exc:
        write_filter.check({"a": 1, "b": 2, "c": 3}, known_keys=frozenset({"a", "b", "c", "name"}))
    assert exc.value.detail["fields"] == ["a", "b", "c"]  # type: ignore[index]


def test_an_unknown_key_is_not_a_permission_problem() -> None:
    """The intake API is required to accept unknown keys with a warning, so an
    unrecognised key passes the filter and is handled by the validator."""
    write_filter = FieldWriteFilter(_grants(edit=frozenset({"name"})))
    write_filter.check({"name": "Ada", "who_knows": 1}, known_keys=frozenset({"name"}))


def test_import_is_a_separate_grant_from_edit() -> None:
    write_filter = FieldWriteFilter(_grants(edit=frozenset({"name"}), import_=frozenset({"phone"})))
    write_filter.check_import(["phone"], known_keys=frozenset({"name", "phone"}))

    with pytest.raises(HTTPException) as exc:
        write_filter.check_import(["name"], known_keys=frozenset({"name", "phone"}))
    assert exc.value.detail["code"] == "field_not_importable"  # type: ignore[index]


# --- the template editor -----------------------------------------------------


async def test_provisioned_templates_get_a_sensible_default_matrix(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Each default template gets the narrowest set that makes its name true."""
    headers = await _admin(api, workspace)
    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=headers)
        ).json()
    }

    caller = (
        await api.get(
            workspace.path(f"/settings/permission-templates/{templates['Caller']}/field-grants"),
            headers=headers,
        )
    ).json()
    assert caller["columns"]["view"]["rollup"] == "Full"
    assert caller["columns"]["edit"]["rollup"] == "Full"
    # Export off by default — the data-exfiltration control from §6.4.
    assert caller["columns"]["export"]["rollup"] == "None"
    assert caller["columns"]["export"]["count"] == 0


async def test_the_matrix_reports_rollups_and_counts(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Computed server-side so the badge cannot disagree with the data."""
    headers = await _admin(api, workspace)
    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=headers)
        ).json()
    }
    base = workspace.path(f"/settings/permission-templates/{templates['Marketing']}/field-grants")
    matrix = (await api.get(base, headers=headers)).json()

    fields = matrix["fields"]
    assert len(fields) == 4  # the four built-ins
    assert matrix["columns"]["view"]["total"] == 4

    # Revoke View on one field: the rollup must become Partial.
    updated = await api.put(
        base,
        headers=headers,
        json={
            "grants": [
                {"field_id": fields[0]["field_id"], "view": False, "edit": False},
            ]
        },
    )
    assert updated.status_code == 200
    assert updated.json()["columns"]["view"]["rollup"] == "Partial"
    assert updated.json()["columns"]["view"]["count"] == 3


async def test_the_column_select_all_sets_one_grant_across_every_field(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=headers)
        ).json()
    }
    base = workspace.path(f"/settings/permission-templates/{templates['Caller']}/field-grants/bulk")

    response = await api.put(base, headers=headers, json={"grant": "EXPORT", "value": True})
    assert response.status_code == 200
    assert response.json()["columns"]["export"]["rollup"] == "Full"

    cleared = await api.put(base, headers=headers, json={"grant": "EXPORT", "value": False})
    assert cleared.json()["columns"]["export"]["rollup"] == "None"


async def test_root_is_read_only(api: AsyncClient, workspace: WorkspaceFixture) -> None:
    """§6.1 — it is the fallback that must keep working when other templates
    are misconfigured."""
    headers = await _admin(api, workspace)
    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=headers)
        ).json()
    }
    root = templates["Root"]

    for method, path, body in (
        ("PATCH", f"/settings/permission-templates/{root}", {"name": "Renamed"}),
        ("DELETE", f"/settings/permission-templates/{root}", None),
        (
            "PUT",
            f"/settings/permission-templates/{root}/field-grants",
            {"grants": []},
        ),
    ):
        response = await api.request(method, workspace.path(path), headers=headers, json=body)
        assert response.status_code == 403, f"{method} {path}"
        assert response.json()["detail"]["code"] == "template_readonly"


async def test_an_assigned_template_cannot_be_deleted(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """Deleting it would leave a membership pointing at nothing."""
    headers = await _admin(api, workspace)
    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=headers)
        ).json()
    }

    response = await api.delete(
        workspace.path(f"/settings/permission-templates/{templates['Caller']}"), headers=headers
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "template_assigned"
    assert detail["assigned"] == 1


async def test_an_unassigned_template_can_be_created_and_deleted(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    created = await api.post(
        workspace.path("/settings/permission-templates"),
        headers=headers,
        json={"name": "Auditor", "capabilities": {"reports": {"view_reports": True}}},
    )
    assert created.status_code == 201
    assert created.json()["capabilities"]["reports"]["view_reports"] is True

    # A new template starts with View on every field and nothing else.
    matrix = (
        await api.get(
            workspace.path(f"/settings/permission-templates/{created.json()['id']}/field-grants"),
            headers=headers,
        )
    ).json()
    assert matrix["columns"]["view"]["rollup"] == "Full"
    assert matrix["columns"]["edit"]["rollup"] == "None"

    deleted = await api.delete(
        workspace.path(f"/settings/permission-templates/{created.json()['id']}"),
        headers=headers,
    )
    assert deleted.status_code == 204


async def test_the_capability_schema_flags_proposed_groups(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """So a reviewer can tell observed contents from proposed ones."""
    headers = await _admin(api, workspace)
    schema = (
        await api.get(
            workspace.path("/settings/permission-templates/capability-schema"),
            headers=headers,
        )
    ).json()

    assert len(schema["access"]) == 10
    assert len(schema["view"]) == 3
    leads = next(g for g in schema["access"] if g["key"] == "leads")
    assert leads["proposed"] is False
    assert "merge_leads" in leads["capabilities"]
    assert all(g["proposed"] for g in schema["access"] if g["key"] != "leads")


async def test_the_lead_view_layout_round_trips(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    """ "Set up your lead view" (§6.3)."""
    headers = await _admin(api, workspace)
    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=headers)
        ).json()
    }
    fields = (await api.get(workspace.path("/settings/lead-fields"), headers=headers)).json()
    base = workspace.path(f"/settings/permission-templates/{templates['Caller']}/lead-view")

    assert (await api.get(base, headers=headers)).json()["layout"] == []

    saved = await api.put(
        base,
        headers=headers,
        json={
            "layout": [
                {"label": "Contact", "collapsed": False, "field_ids": [fields[0]["id"]]},
                {"label": "Other", "collapsed": True, "field_ids": [fields[1]["id"]]},
            ]
        },
    )
    assert saved.status_code == 200
    layout = saved.json()["layout"]
    assert [g["label"] for g in layout] == ["Contact", "Other"]
    assert layout[1]["collapsed"] is True

    assert (await api.get(base, headers=headers)).json()["layout"] == layout


async def test_a_lead_view_cannot_name_an_unknown_field(
    api: AsyncClient, workspace: WorkspaceFixture
) -> None:
    headers = await _admin(api, workspace)
    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=headers)
        ).json()
    }
    response = await api.put(
        workspace.path(f"/settings/permission-templates/{templates['Caller']}/lead-view"),
        headers=headers,
        json={"layout": [{"label": "X", "field_ids": [str(uuid.uuid4())]}]},
    )
    assert response.status_code == 404


async def test_a_rep_cannot_edit_the_matrix(api: AsyncClient, workspace: WorkspaceFixture) -> None:
    rep = workspace.members["rep"]
    await login(api, rep)
    templates = {
        t["name"]: t["id"]
        for t in (
            await api.get(workspace.path("/settings/permission-templates"), headers=rep.auth)
        ).json()
    }
    response = await api.put(
        workspace.path(f"/settings/permission-templates/{templates['Caller']}/field-grants"),
        headers=rep.auth,
        json={"grants": []},
    )
    assert response.status_code == 403
