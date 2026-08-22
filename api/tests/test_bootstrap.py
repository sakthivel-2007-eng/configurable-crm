"""Creating the first account (M11).

Invite-only is a closed loop: an account exists because somebody with an account
invited it. A fresh deployment has none, so something has to open the loop from
outside — and it has to be a command rather than an endpoint, because an
endpoint that mints the first owner is an endpoint an attacker races the
operator to.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import build_workspace

from app.auth.passwords import PasswordHasherService
from app.bootstrap.first_account import (
    AlreadyBootstrappedError,
    create_first_account,
)
from app.config import Settings
from app.models import Membership, PasswordResetToken, PermissionTemplate, User

pytestmark = pytest.mark.integration


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


async def test_it_creates_a_workspace_an_owner_and_a_link(
    db_session: AsyncSession, settings: Settings
) -> None:
    result = await create_first_account(
        db_session,
        settings,
        email="Founder@Example.com",
        full_name="A Founder",
        workspace_name="Acme Tutors",
    )

    assert result.workspace_name == "Acme Tutors"
    assert result.email == "founder@example.com", "addresses are case-folded"
    assert "/set-password?token=" in result.set_password_url

    # A Root membership, which is what makes them able to invite anyone else.
    membership = (
        await db_session.execute(
            select(Membership).join(User).where(User.email == "founder@example.com")
        )
    ).scalar_one()
    template = await db_session.get(PermissionTemplate, membership.template_id)
    assert template is not None
    assert template.name == "Root"
    assert membership.has_license, "an owner who cannot log in is not an owner"


async def test_the_owner_never_gets_a_password_from_the_operator(
    db_session: AsyncSession, settings: Settings
) -> None:
    """The operator running the command must not learn the credential.

    So it issues the same single-use invitation token the invite flow issues,
    rather than printing a password. One path into the product, not two.
    """
    result = await create_first_account(
        db_session,
        settings,
        email="founder@example.com",
        full_name="A Founder",
        workspace_name="Acme",
    )

    token = (await db_session.execute(select(PasswordResetToken))).scalars().one()
    assert token.purpose == "INVITE"
    assert token.used_at is None
    # The link carries the plaintext; the row carries only its hash.
    assert token.token_hash not in result.set_password_url


async def test_it_refuses_when_the_deployment_already_has_users(
    db_session: AsyncSession, settings: Settings, hasher: PasswordHasherService
) -> None:
    """The whole safety of the command.

    A mistyped re-run against a live install is exactly the sort of thing that
    gets discovered late.
    """
    await build_workspace(db_session, hasher, name="Existing", owner_email="already@example.com")

    with pytest.raises(AlreadyBootstrappedError):
        await create_first_account(
            db_session,
            settings,
            email="second@example.com",
            full_name="Second",
            workspace_name="Another",
        )


async def test_force_allows_a_genuine_second_workspace(
    db_session: AsyncSession, settings: Settings, hasher: PasswordHasherService
) -> None:
    await build_workspace(db_session, hasher, name="Existing", owner_email="already@example.com")

    result = await create_first_account(
        db_session,
        settings,
        email="second@example.com",
        full_name="Second",
        workspace_name="Another",
        force=True,
    )
    assert result.workspace_name == "Another"


async def test_the_new_workspace_carries_no_business_taxonomy(
    db_session: AsyncSession, settings: Settings
) -> None:
    """The #1 trap in this codebase, checked at the one place a real customer
    workspace is born.

    `test_provisioning.py` asserts this for `POST /workspaces`; bootstrap is the
    *other* creation path, and it would be a poor joke to seed a demo taxonomy
    into the very first workspace a customer ever sees.
    """
    from app.models import CallDisposition, LeadField, Stage

    await create_first_account(
        db_session,
        settings,
        email="founder@example.com",
        full_name="A Founder",
        workspace_name="Acme",
    )

    labels: list[str] = []
    for model, column in ((LeadField, "label"), (Stage, "label"), (CallDisposition, "label")):
        rows = await db_session.execute(select(model))
        labels += [getattr(row, column).lower() for row in rows.scalars().all()]

    for forbidden in ("course", "student", "enquiry", "demo booked", "tutor", "admission"):
        assert not any(forbidden in label for label in labels), (
            f"bootstrap seeded {forbidden!r} into a customer's first workspace"
        )
