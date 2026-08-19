"""Member lifecycle: hierarchy, licensing, availability, deactivation."""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import (
    WorkspaceFixture,
    add_member,
    build_workspace,
    login,
)

from app.auth.passwords import PasswordHasherService
from app.tenancy.session import ScopedSession

pytestmark = pytest.mark.integration


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


@pytest.fixture
async def team(
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    api: AsyncClient,
) -> WorkspaceFixture:
    """Owner (Root) → manager (Manager) → rep (Caller), all logged in."""
    fixture = await build_workspace(
        db_session,
        hasher,
        name="Team",
        owner_email="team-owner@example.com",
    )
    fixture.workspace.seat_limit = 10
    await db_session.commit()

    manager = await add_member(
        db_session,
        hasher,
        fixture,
        key="manager",
        email="team-manager@example.com",
        template_name="Manager",
    )
    await add_member(
        db_session,
        hasher,
        fixture,
        key="rep",
        email="team-rep@example.com",
        template_name="Caller",
        manager=manager,
    )

    await login(api, fixture.owner)
    for actor in fixture.members.values():
        await login(api, actor)
    return fixture


# --- hierarchy visibility ----------------------------------------------------


async def test_a_manager_sees_themselves_and_their_reports(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    """The visibility rule lives in the scoping layer, so it applies to the
    list endpoint without that endpoint implementing it."""
    manager = team.members["manager"]
    response = await api.get(team.path("/members"), headers=manager.auth)

    assert response.status_code == 200
    returned = {item["id"] for item in response.json()["items"]}
    assert returned == {
        str(manager.membership.id),
        str(team.members["rep"].membership.id),
    }
    assert str(team.owner.membership.id) not in returned


async def test_a_rep_sees_only_themselves(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    rep = team.members["rep"]
    response = await api.get(team.path("/members"), headers=rep.auth)

    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == {str(rep.membership.id)}


async def test_an_admin_sees_everyone(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.get(team.path("/members"), headers=team.owner.auth)
    assert response.status_code == 200
    assert response.json()["total"] == 3


async def test_hierarchy_nests_reports_under_their_manager(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.get(team.path("/members/hierarchy"), headers=team.owner.auth)
    assert response.status_code == 200

    tree = response.json()
    manager_node = next(
        node for node in tree if node["member"]["id"] == str(team.members["manager"].membership.id)
    )
    assert [child["member"]["id"] for child in manager_node["reports"]] == [
        str(team.members["rep"].membership.id)
    ]


async def test_a_manager_cycle_is_refused(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    """Making the manager report to their own report would make the visibility
    CTE loop and let a rep see their manager's reports."""
    response = await api.patch(
        team.path(f"/members/{team.members['manager'].membership.id}"),
        headers=team.owner.auth,
        json={"manager_id": str(team.members["rep"].membership.id)},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "manager_cycle"


async def test_a_member_cannot_manage_themselves(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.patch(
        team.path(f"/members/{team.members['rep'].membership.id}"),
        headers=team.owner.auth,
        json={"manager_id": str(team.members["rep"].membership.id)},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_manager"


# --- licensing ---------------------------------------------------------------


async def test_licences_are_capped_by_the_workspace_seat_limit(
    api: AsyncClient,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    team: WorkspaceFixture,
) -> None:
    team.workspace.seat_limit = 3
    await db_session.commit()

    extra = await add_member(
        db_session,
        hasher,
        team,
        key="extra",
        email="extra@example.com",
        template_name="Caller",
        has_license=False,
    )

    response = await api.post(
        team.path(f"/members/{extra.membership.id}/license"),
        headers=team.owner.auth,
    )

    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "seat_limit_reached"
    assert body["seat_limit"] == 3
    assert body["seats_used"] == 3


async def test_revoking_a_licence_frees_the_seat(
    api: AsyncClient,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    team: WorkspaceFixture,
) -> None:
    team.workspace.seat_limit = 3
    await db_session.commit()

    extra = await add_member(
        db_session,
        hasher,
        team,
        key="extra",
        email="extra2@example.com",
        template_name="Caller",
        has_license=False,
    )

    revoked = await api.delete(
        team.path(f"/members/{team.members['rep'].membership.id}/license"),
        headers=team.owner.auth,
    )
    assert revoked.status_code == 200
    assert revoked.json()["has_license"] is False

    granted = await api.post(
        team.path(f"/members/{extra.membership.id}/license"),
        headers=team.owner.auth,
    )
    assert granted.status_code == 200
    assert granted.json()["has_license"] is True


async def test_an_unlicensed_member_cannot_use_the_api(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    """The scoping dependency re-checks the licence per request, so revocation
    takes effect immediately rather than at the next token refresh."""
    rep = team.members["rep"]
    assert (await api.get(team.path("/members"), headers=rep.auth)).status_code == 200

    await api.delete(
        team.path(f"/members/{rep.membership.id}/license"),
        headers=team.owner.auth,
    )

    after = await api.get(team.path("/members"), headers=rep.auth)
    assert after.status_code == 403
    assert after.json()["detail"]["code"] == "no_license"


async def test_seat_limit_cannot_be_lowered_below_current_usage(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.patch(
        team.path(),
        headers=team.owner.auth,
        json={"seat_limit": 1},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "seat_limit_below_usage"


# --- availability ------------------------------------------------------------


async def test_availability_change_is_recorded_in_the_log(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    rep = team.members["rep"]

    updated = await api.put(
        team.path(f"/members/{rep.membership.id}/availability"),
        headers=rep.auth,
        json={"status": "ON_LEAVE", "note": "Annual leave"},
    )
    assert updated.status_code == 200
    assert updated.json()["availability"] == "ON_LEAVE"

    log = await api.get(
        team.path(f"/members/{rep.membership.id}/availability-log"),
        headers=rep.auth,
    )
    assert log.status_code == 200
    entries = log.json()["items"]
    assert entries[0]["status"] == "ON_LEAVE"
    assert entries[0]["note"] == "Annual leave"
    assert entries[0]["changed_by_id"] == str(rep.membership.id)


async def test_a_rep_cannot_change_someone_elses_availability(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.put(
        team.path(f"/members/{team.members['manager'].membership.id}/availability"),
        headers=team.members["rep"].auth,
        json={"status": "ON_LEAVE"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "not_own_membership"


async def test_availability_cannot_be_set_to_inactive_directly(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    """INACTIVE is deactivation, and deactivation must reassign the pipeline
    first. Allowing it here would be the orphan-pipeline bug with extra steps."""
    response = await api.put(
        team.path(f"/members/{team.members['rep'].membership.id}/availability"),
        headers=team.owner.auth,
        json={"status": "INACTIVE"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "use_deactivate_endpoint"


# --- deactivation ------------------------------------------------------------


async def test_deactivation_refuses_when_the_member_holds_open_leads(
    api: AsyncClient,
    wired_app: FastAPI,
    team: WorkspaceFixture,
) -> None:
    """The rule this milestone exists to protect.

    Leads arrive in M5, so the ownership port is stubbed to report a pipeline.
    From M5 the real implementation reports the true count and this test
    keeps passing unchanged.
    """
    from app.services.lead_ownership import get_lead_ownership

    class HoldsLeads:
        async def count_open_leads(self, session: ScopedSession, membership_id: object) -> int:
            return 42

        async def reassign_open_leads(
            self,
            session: ScopedSession,
            *,
            from_membership_id: object,
            to_membership_id: object,
        ) -> int:
            return 42

    wired_app.dependency_overrides[get_lead_ownership] = HoldsLeads

    response = await api.post(
        team.path(f"/members/{team.members['rep'].membership.id}/deactivate"),
        headers=team.owner.auth,
        json={},
    )

    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "reassignment_required"
    assert body["open_lead_count"] == 42


async def test_deactivation_with_a_target_transfers_the_pipeline(
    api: AsyncClient,
    wired_app: FastAPI,
    team: WorkspaceFixture,
) -> None:
    from app.services.lead_ownership import get_lead_ownership

    transfers: list[tuple[object, object]] = []

    class HoldsLeads:
        async def count_open_leads(self, session: ScopedSession, membership_id: object) -> int:
            return 7

        async def reassign_open_leads(
            self,
            session: ScopedSession,
            *,
            from_membership_id: object,
            to_membership_id: object,
        ) -> int:
            transfers.append((from_membership_id, to_membership_id))
            return 7

    wired_app.dependency_overrides[get_lead_ownership] = HoldsLeads

    rep = team.members["rep"]
    manager = team.members["manager"]

    response = await api.post(
        team.path(f"/members/{rep.membership.id}/deactivate"),
        headers=team.owner.auth,
        json={"reassign_to_membership_id": str(manager.membership.id)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["leads_reassigned"] == 7
    assert body["member"]["is_active"] is False
    assert body["member"]["has_license"] is False
    assert body["member"]["availability"] == "INACTIVE"
    assert transfers == [(rep.membership.id, manager.membership.id)]


async def test_deactivation_succeeds_with_no_pipeline_to_move(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    """With the M1 null ownership port, nobody holds leads, so no target is
    required. The endpoint must not demand one gratuitously."""
    response = await api.post(
        team.path(f"/members/{team.members['rep'].membership.id}/deactivate"),
        headers=team.owner.auth,
        json={},
    )
    assert response.status_code == 200
    assert response.json()["leads_reassigned"] == 0


async def test_deactivating_a_manager_reparents_their_reports(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    """Otherwise the rep dangles under a deactivated manager and drops out of
    their skip-level's visibility set."""
    await api.post(
        team.path(f"/members/{team.members['manager'].membership.id}/deactivate"),
        headers=team.owner.auth,
        json={},
    )

    rep = await api.get(
        team.path(f"/members/{team.members['rep'].membership.id}"),
        headers=team.owner.auth,
    )
    assert rep.status_code == 200
    assert rep.json()["manager_id"] is None


async def test_you_cannot_deactivate_yourself(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.post(
        team.path(f"/members/{team.owner.membership.id}/deactivate"),
        headers=team.owner.auth,
        json={},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "cannot_deactivate_self"


async def test_a_deactivated_member_cannot_log_in(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    from tests.factories import TEST_PASSWORD

    rep = team.members["rep"]
    await api.post(
        team.path(f"/members/{rep.membership.id}/deactivate"),
        headers=team.owner.auth,
        json={},
    )

    response = await api.post(
        "/api/v1/auth/login",
        json={"email": rep.user.email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "member_inactive"


async def test_reactivation_restores_the_licence(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    rep = team.members["rep"]
    await api.post(
        team.path(f"/members/{rep.membership.id}/deactivate"),
        headers=team.owner.auth,
        json={},
    )

    response = await api.post(
        team.path(f"/members/{rep.membership.id}/reactivate"),
        headers=team.owner.auth,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_active"] is True
    assert body["has_license"] is True
    assert body["availability"] == "WORKING"


async def test_reactivation_needs_a_free_seat(
    api: AsyncClient,
    db_session: AsyncSession,
    team: WorkspaceFixture,
) -> None:
    rep = team.members["rep"]
    await api.post(
        team.path(f"/members/{rep.membership.id}/deactivate"),
        headers=team.owner.auth,
        json={},
    )

    team.workspace.seat_limit = 2
    await db_session.commit()

    response = await api.post(
        team.path(f"/members/{rep.membership.id}/reactivate"),
        headers=team.owner.auth,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "seat_limit_reached"


# --- permissions on the admin endpoints --------------------------------------


async def test_a_rep_cannot_invite_members(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.post(
        team.path("/members"),
        headers=team.members["rep"].auth,
        json={
            "email": "smuggled@example.com",
            "full_name": "Smuggled",
            "template_id": str(team.templates["Caller"].id),
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "insufficient_permissions"


async def test_a_rep_cannot_deactivate_anyone(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.post(
        team.path(f"/members/{team.members['manager'].membership.id}/deactivate"),
        headers=team.members["rep"].auth,
        json={},
    )
    assert response.status_code == 403


# --- invitation and bulk upload ----------------------------------------------


async def test_inviting_a_new_user_creates_the_account_and_membership(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.post(
        team.path("/members"),
        headers=team.owner.auth,
        json={
            "email": "Newcomer@Example.Com",
            "full_name": "Newcomer",
            "template_id": str(team.templates["Caller"].id),
            "grant_license": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "newcomer@example.com"
    assert body["has_license"] is True
    assert body["template_name"] == "Caller"


async def test_inviting_the_same_user_twice_is_a_conflict(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.post(
        team.path("/members"),
        headers=team.owner.auth,
        json={
            "email": team.members["rep"].user.email,
            "full_name": "Duplicate",
            "template_id": str(team.templates["Caller"].id),
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "member_exists"


def _workbook(rows: list[dict[str, str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    headers = ["email", "full_name", "template", "manager_email", "license"]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def test_bulk_upload_dry_run_reports_without_writing(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    content = _workbook(
        [
            {"email": "bulk1@example.com", "full_name": "Bulk One", "template": "Caller"},
            {"email": "bulk2@example.com", "full_name": "Bulk Two", "template": "Manager"},
        ]
    )

    preview = await api.post(
        team.path("/members/bulk-upload"),
        headers=team.owner.auth,
        params={"dry_run": True},
        files={"file": ("members.xlsx", content, "application/vnd.ms-excel")},
    )

    assert preview.status_code == 200
    report = preview.json()
    assert report["dry_run"] is True
    assert report["created"] == 2
    assert report["errored"] == 0

    members = await api.get(team.path("/members"), headers=team.owner.auth)
    assert members.json()["total"] == 3, "A dry run must not create anyone"


async def test_bulk_upload_commit_creates_members(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    content = _workbook(
        [
            {
                "email": "bulk3@example.com",
                "full_name": "Bulk Three",
                "template": "Caller",
                "license": "yes",
            },
        ]
    )

    response = await api.post(
        team.path("/members/bulk-upload"),
        headers=team.owner.auth,
        params={"dry_run": False},
        files={"file": ("members.xlsx", content, "application/vnd.ms-excel")},
    )

    assert response.status_code == 200
    assert response.json()["created"] == 1

    members = await api.get(team.path("/members"), headers=team.owner.auth)
    emails = {item["user"]["email"] for item in members.json()["items"]}
    assert "bulk3@example.com" in emails


async def test_bulk_upload_reports_bad_rows_without_failing_good_ones(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    """Rejecting the whole file because row 2 has a typo makes people edit
    spreadsheets in the dark."""
    content = _workbook(
        [
            {"email": "good@example.com", "full_name": "Good", "template": "Caller"},
            {"email": "bad@example.com", "full_name": "Bad", "template": "Nonexistent"},
            {"email": "", "full_name": "No Email", "template": "Caller"},
        ]
    )

    response = await api.post(
        team.path("/members/bulk-upload"),
        headers=team.owner.auth,
        params={"dry_run": False},
        files={"file": ("members.xlsx", content, "application/vnd.ms-excel")},
    )

    assert response.status_code == 200
    report = response.json()
    assert report["created"] == 1
    assert report["errored"] == 2
    assert any("Nonexistent" in (row["message"] or "") for row in report["rows"])


async def test_bulk_upload_rejects_a_workbook_missing_required_columns(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    workbook = Workbook()
    workbook.active.append(["email", "full_name"])  # no template column
    buffer = BytesIO()
    workbook.save(buffer)

    response = await api.post(
        team.path("/members/bulk-upload"),
        headers=team.owner.auth,
        files={"file": ("members.xlsx", buffer.getvalue(), "application/vnd.ms-excel")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "missing_columns"


async def test_bulk_upload_rejects_a_file_that_is_not_a_workbook(
    api: AsyncClient,
    team: WorkspaceFixture,
) -> None:
    response = await api.post(
        team.path("/members/bulk-upload"),
        headers=team.owner.auth,
        files={"file": ("members.xlsx", b"this is not xlsx", "application/vnd.ms-excel")},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_workbook"
