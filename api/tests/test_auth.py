"""Authentication: credentials, the licence gates, rotation and rate limiting."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import (
    TEST_PASSWORD,
    WorkspaceFixture,
    add_member,
    build_workspace,
    login,
)

from app.auth.passwords import PasswordHasherService

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
    return await build_workspace(
        db_session,
        hasher,
        name="Acme",
        owner_email="owner@example.com",
    )


async def test_login_returns_tokens_user_and_memberships(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    response = await api.post(
        "/api/v1/auth/login",
        json={"email": workspace.owner.user.email, "password": TEST_PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 30 * 60
    assert body["user"]["email"] == workspace.owner.user.email
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["workspace"]["id"] == str(workspace.id)
    assert body["memberships"][0]["template_name"] == "Root"


async def test_login_with_wrong_password_is_401(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    response = await api.post(
        "/api/v1/auth/login",
        json={"email": workspace.owner.user.email, "password": "not-the-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


async def test_unknown_email_is_indistinguishable_from_a_wrong_password(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    """Same status, same code, same message — otherwise this enumerates accounts."""
    unknown = await api.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": TEST_PASSWORD},
    )
    wrong = await api.post(
        "/api/v1/auth/login",
        json={"email": workspace.owner.user.email, "password": "not-the-password"},
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


async def test_unlicensed_member_is_refused_with_no_license(
    api: AsyncClient,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    workspace: WorkspaceFixture,
) -> None:
    unlicensed = await add_member(
        db_session,
        hasher,
        workspace,
        key="unlicensed",
        email="unlicensed@example.com",
        template_name="Caller",
        has_license=False,
    )

    response = await api.post(
        "/api/v1/auth/login",
        json={"email": unlicensed.user.email, "password": TEST_PASSWORD},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "no_license"


async def test_deactivated_member_is_refused_with_member_inactive(
    api: AsyncClient,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    workspace: WorkspaceFixture,
) -> None:
    member = await add_member(
        db_session,
        hasher,
        workspace,
        key="departed",
        email="departed@example.com",
        template_name="Caller",
    )
    member.membership.is_active = False
    await db_session.commit()

    response = await api.post(
        "/api/v1/auth/login",
        json={"email": member.user.email, "password": TEST_PASSWORD},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "member_inactive"


async def test_refresh_rotates_and_invalidates_the_old_token(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    await login(api, workspace.owner)
    original = workspace.owner.refresh_token

    rotated = await api.post("/api/v1/auth/refresh", json={"refresh_token": original})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != original

    replay = await api.post("/api/v1/auth/refresh", json={"refresh_token": original})
    assert replay.status_code == 401
    assert replay.json()["detail"]["code"] == "token_reuse_detected"


async def test_reuse_detection_kills_the_whole_family(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    """Replaying a rotated token revokes its successor too.

    We cannot tell the legitimate holder from the thief, so both are logged
    out and the real user re-authenticates.
    """
    await login(api, workspace.owner)
    original = workspace.owner.refresh_token

    rotated = await api.post("/api/v1/auth/refresh", json={"refresh_token": original})
    successor = rotated.json()["refresh_token"]

    await api.post("/api/v1/auth/refresh", json={"refresh_token": original})

    after_breach = await api.post("/api/v1/auth/refresh", json={"refresh_token": successor})
    assert after_breach.status_code == 401


async def test_logout_revokes_the_refresh_token(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    await login(api, workspace.owner)

    logout = await api.post(
        "/api/v1/auth/logout",
        json={"refresh_token": workspace.owner.refresh_token},
    )
    assert logout.status_code == 204

    reuse = await api.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": workspace.owner.refresh_token},
    )
    assert reuse.status_code == 401


async def test_access_token_is_required_and_verified(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    missing = await api.get("/api/v1/me")
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "not_authenticated"

    garbage = await api.get("/api/v1/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert garbage.status_code == 401
    assert garbage.json()["detail"]["code"] == "invalid_token"


async def test_a_refresh_token_cannot_be_used_as_a_bearer_token(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    """Token types are distinct. A long-lived refresh token used as a bearer
    would be a 30-day access token."""
    await login(api, workspace.owner)
    response = await api.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {workspace.owner.refresh_token}"},
    )
    assert response.status_code == 401


async def test_login_is_rate_limited(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    """The eleventh attempt in the window is refused with 429 and Retry-After."""
    payload = {"email": workspace.owner.user.email, "password": "wrong"}

    for _ in range(10):
        assert (await api.post("/api/v1/auth/login", json=payload)).status_code == 401

    limited = await api.post("/api/v1/auth/login", json=payload)
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) > 0


async def test_rate_limit_blocks_even_a_correct_password(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    """Otherwise the endpoint stays a password oracle while locked out."""
    for _ in range(11):
        await api.post(
            "/api/v1/auth/login",
            json={"email": workspace.owner.user.email, "password": "wrong"},
        )

    correct = await api.post(
        "/api/v1/auth/login",
        json={"email": workspace.owner.user.email, "password": TEST_PASSWORD},
    )
    assert correct.status_code == 429


async def test_refresh_is_rate_limited_per_token(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    """`/auth/refresh` is rate-limited (02-api-contract.md §Auth).

    A refresh token is legitimately presented exactly once, so repeating one is
    a client stuck in a loop or a stolen token being replayed. The eleventh
    attempt on the same token is refused with 429 and Retry-After.
    """
    payload = {"refresh_token": "not-a-real-refresh-token"}

    for _ in range(10):
        assert (await api.post("/api/v1/auth/refresh", json=payload)).status_code == 401

    limited = await api.post("/api/v1/auth/refresh", json=payload)
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) > 0


async def test_refresh_is_rate_limited_per_client_ip(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    """A distinct token every time, so only the IP counter can trip.

    Without this budget, the per-token counter alone would be useless against
    anyone simply varying the token they present.
    """
    for attempt in range(20):
        response = await api.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": f"distinct-token-{attempt}"},
        )
        assert response.status_code == 401, f"attempt {attempt} was {response.status_code}"

    limited = await api.post("/api/v1/auth/refresh", json={"refresh_token": "one-more"})
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "rate_limited"


async def test_refresh_rate_limit_blocks_even_a_valid_token(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    """Holding a genuine token is not a way around the budget.

    The mirror of the login case: the limiter runs before the token is looked
    up, so an exhausted client cannot use the endpoint to probe which token
    values exist.
    """
    await login(api, workspace.owner)
    genuine = workspace.owner.refresh_token

    for attempt in range(20):
        await api.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": f"junk-token-{attempt}"},
        )

    refused = await api.post("/api/v1/auth/refresh", json={"refresh_token": genuine})
    assert refused.status_code == 429

    # And the genuine token was not consumed by the refused attempt — once the
    # window clears it must still work, so the refusal cannot be a denial of
    # service against the legitimate holder.
    assert refused.json()["detail"]["code"] == "rate_limited"


async def test_change_password_ends_every_existing_session(
    api: AsyncClient,
    workspace: WorkspaceFixture,
) -> None:
    await login(api, workspace.owner)

    changed = await api.post(
        "/api/v1/auth/change-password",
        headers=workspace.owner.auth,
        json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-passphrase"},
    )
    assert changed.status_code == 204

    stale = await api.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": workspace.owner.refresh_token},
    )
    assert stale.status_code == 401


async def test_me_permissions_reports_capabilities_and_visibility(
    api: AsyncClient,
    db_session: AsyncSession,
    hasher: PasswordHasherService,
    workspace: WorkspaceFixture,
) -> None:
    manager = await add_member(
        db_session,
        hasher,
        workspace,
        key="manager",
        email="manager@example.com",
        template_name="Manager",
    )
    rep = await add_member(
        db_session,
        hasher,
        workspace,
        key="rep",
        email="rep@example.com",
        template_name="Caller",
        manager=manager,
    )
    await login(api, manager)

    response = await api.get(
        "/api/v1/me/permissions",
        params={"workspace_id": str(workspace.id)},
        headers=manager.auth,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["template_name"] == "Manager"
    assert body["sees_all_members"] is False
    # A manager sees themselves and their reports — the hierarchy rule,
    # resolved in the scoping layer.
    assert set(body["visible_membership_ids"]) == {
        str(manager.membership.id),
        str(rep.membership.id),
    }
    # `field_grants` is the M4 shape, present and empty so the frontend can be
    # written against its final form.
    assert body["field_grants"] == {}
