"""Invitations and password resets (M11).

The workspace model is invite-only, so this is **the** account-creation path —
there is no registration endpoint and no other way in. Until M11 it was a dead
end: `invite_member` created an account with an unguessable password and sent
nothing, and the password reset its docstring promised did not exist.

Most of these assert security properties rather than behaviour, because that is
where this kind of code goes wrong:

- requesting a reset must not reveal whether an address has an account
- every rejection must look identical, whatever was wrong with the token
- a token is single-use
- redeeming one must end existing sessions
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import TEST_PASSWORD, WorkspaceFixture, build_workspace, login

from app.auth.passwords import PasswordHasherService
from app.models import PasswordResetToken, RefreshToken, User
from app.services.credentials import INVITE_TTL, RESET_TTL, CredentialService, TokenPurpose

pytestmark = pytest.mark.integration


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


@pytest.fixture
def mailbox(wired_app: FastAPI) -> object:
    """A recording sender, so nothing can reach a real address from a test."""
    from app.services.email import RecordingEmailSender

    sender = RecordingEmailSender()
    wired_app.state.email_sender = sender
    return sender


@pytest.fixture
async def ws(
    db_session: AsyncSession, hasher: PasswordHasherService, api: AsyncClient
) -> WorkspaceFixture:
    fixture = await build_workspace(
        db_session, hasher, name="Invite Co", owner_email="invite-owner@example.com"
    )
    await login(api, fixture.owner)
    return fixture


# --- the dead end this milestone closed --------------------------------------


async def test_an_invited_person_can_actually_sign_in(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    mailbox: object,
) -> None:
    """The whole point.

    Before M11 this was impossible: the invite created an account with a random
    password nobody was told, and the reset flow its docstring referred to did
    not exist. Every invited person held an account they could never use.
    """
    invited = await api.post(
        ws.path("/members"),
        headers=ws.owner.auth,
        json={
            "email": "newcomer@example.com",
            "full_name": "New Comer",
            "template_id": str(ws.templates["Caller"].id),
            "grant_license": True,
        },
    )
    assert invited.status_code == 201, invited.text

    # The invitation actually sent something.
    sent = mailbox.sent
    assert len(sent) == 1, "the invite sent no email"
    assert sent[0].to == ("newcomer@example.com",)
    assert "set-password?token=" in sent[0].body

    token = sent[0].body.split("set-password?token=")[1].split()[0]
    confirmed = await api.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "a-brand-new-password-99"},
    )
    assert confirmed.status_code == 200, confirmed.text

    signed_in = await api.post(
        "/api/v1/auth/login",
        json={"email": "newcomer@example.com", "password": "a-brand-new-password-99"},
    )
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["access_token"]


# --- not a membership oracle -------------------------------------------------


async def test_requesting_a_reset_says_the_same_thing_either_way(
    api: AsyncClient, ws: WorkspaceFixture, mailbox: object
) -> None:
    """Otherwise the login page becomes a membership oracle.

    A prober works through a list of addresses and learns which have accounts —
    and for a B2B CRM, "does this company use this product" is itself worth
    something. The mail, or its absence, is the only difference.
    """
    real = await api.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "invite-owner@example.com"},
    )
    unknown = await api.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "nobody-at-all@example.com"},
    )

    assert real.status_code == unknown.status_code == 202
    assert real.json() == unknown.json()

    sent = mailbox.sent
    assert len(sent) == 1, "a link went to an address with no account"
    assert sent[0].to == ("invite-owner@example.com",)


# --- token handling ----------------------------------------------------------


async def test_a_token_works_once(api: AsyncClient, ws: WorkspaceFixture, mailbox: object) -> None:
    await api.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "invite-owner@example.com"},
    )
    token = mailbox.sent[0].body.split("set-password?token=")[1].split()[0]

    first = await api.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "first-choice-password-1"},
    )
    assert first.status_code == 200, first.text

    second = await api.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "second-choice-password-2"},
    )
    assert second.status_code == 400
    assert second.json()["detail"]["code"] == "invalid_token"


async def test_every_bad_token_is_refused_identically(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    mailbox: object,
) -> None:
    """Expired, spent and never-existed must be indistinguishable.

    Otherwise somebody working through guesses learns which were once real.
    """
    await api.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "invite-owner@example.com"},
    )
    token = mailbox.sent[0].body.split("set-password?token=")[1].split()[0]

    # Age it past expiry.
    row = (await db_session.execute(select(PasswordResetToken))).scalars().first()
    assert row is not None
    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    await db_session.commit()

    expired = await api.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "does-not-matter-here-1"},
    )
    invented = await api.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "completely-made-up", "new_password": "does-not-matter-here-1"},
    )

    assert expired.status_code == invented.status_code == 400
    assert expired.json() == invented.json()


async def test_a_new_token_supersedes_the_outstanding_one(
    api: AsyncClient, ws: WorkspaceFixture, mailbox: object
) -> None:
    """Two live tokens mean two chances for an old email to still work."""
    for _ in range(2):
        await api.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "invite-owner@example.com"},
        )
    sent = mailbox.sent
    first = sent[0].body.split("set-password?token=")[1].split()[0]
    second = sent[1].body.split("set-password?token=")[1].split()[0]

    stale = await api.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": first, "new_password": "using-the-old-link-11"},
    )
    assert stale.status_code == 400

    fresh = await api.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": second, "new_password": "using-the-new-link-22"},
    )
    assert fresh.status_code == 200, fresh.text


async def test_redeeming_a_token_ends_every_existing_session(
    api: AsyncClient,
    ws: WorkspaceFixture,
    db_session: AsyncSession,
    mailbox: object,
) -> None:
    """Somebody resetting a password often believes they are compromised.

    Leaving their other sessions live would make the reset theatre.
    """
    live = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))))
        .scalars()
        .all()
    )
    assert live, "the fixture logged in, so there is a session to revoke"

    await api.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "invite-owner@example.com"},
    )
    token = mailbox.sent[0].body.split("set-password?token=")[1].split()[0]
    await api.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "rotated-after-a-scare-7"},
    )

    still_live = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))))
        .scalars()
        .all()
    )
    assert not still_live, "a session survived a password reset"

    # And the old password no longer works.
    old = await api.post(
        "/api/v1/auth/login",
        json={"email": "invite-owner@example.com", "password": TEST_PASSWORD},
    )
    assert old.status_code == 401


async def test_the_token_is_never_stored_in_readable_form(
    api: AsyncClient, ws: WorkspaceFixture, db_session: AsyncSession, mailbox: object
) -> None:
    """A dump of this table must not let anyone become any user."""
    await api.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "invite-owner@example.com"},
    )
    token = mailbox.sent[0].body.split("set-password?token=")[1].split()[0]

    row = (await db_session.execute(select(PasswordResetToken))).scalars().first()
    assert row is not None
    assert row.token_hash != token
    assert token not in row.token_hash
    assert len(row.token_hash) == 64, "sha-256 hex, like refresh tokens"


async def test_an_invitation_lives_longer_than_a_reset(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """An invite must survive a weekend in an inbox; a reset need not.

    A long window on mail somebody did not ask for is a long window for a
    leaked mailbox.
    """
    user = User(email="ttl@example.com", full_name="TTL", password_hash=hasher.hash("x" * 12))
    db_session.add(user)
    await db_session.flush()

    service = CredentialService(db_session)
    invite = await service.issue(user=user, purpose=TokenPurpose.INVITE)
    reset = await service.issue(user=user, purpose=TokenPurpose.RESET)
    await db_session.commit()

    assert invite.expires_at > reset.expires_at
    assert INVITE_TTL > RESET_TTL
