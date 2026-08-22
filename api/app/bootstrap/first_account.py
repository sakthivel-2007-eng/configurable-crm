"""Provisioning the first workspace and its owner (M11)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import PasswordHasherService
from app.config import Settings
from app.models import User, Workspace
from app.services.credentials import CredentialService, TokenPurpose
from app.services.provisioning import WorkspaceProvisioner

__all__ = ["BootstrapResult", "create_first_account"]


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    workspace_name: str
    email: str
    set_password_url: str


class AlreadyBootstrappedError(RuntimeError):
    """Raised when the deployment already has accounts."""


async def create_first_account(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    full_name: str,
    workspace_name: str,
    force: bool = False,
) -> BootstrapResult:
    """Create the owner and their workspace, and return a set-password link.

    Refuses if any user already exists. That guard is the whole safety of this
    command: without it, a stray re-run on a live deployment would mint a second
    owner of a *new* workspace — harmless — but a mistyped one against an
    existing install is exactly the sort of thing that gets discovered late.
    `--force` exists for the genuine second-workspace case and says so out loud.
    """
    existing = await session.execute(select(func.count()).select_from(User))
    if int(existing.scalar() or 0) > 0 and not force:
        raise AlreadyBootstrappedError(
            "This deployment already has users. Invite people from the app "
            "instead, or pass --force if you genuinely want another workspace."
        )

    email = email.casefold()
    found = await session.execute(select(User).where(User.email == email).limit(1))
    user = found.scalar_one_or_none()

    if user is None:
        user = User(
            email=email,
            full_name=full_name,
            # Unguessable and never disclosed. The operator running this command
            # does not learn the owner's credential; the owner sets it from the
            # emailed link, exactly as an invited colleague would.
            password_hash=PasswordHasherService(settings).hash(secrets.token_urlsafe(32)),
        )
        session.add(user)
        await session.flush()

    workspace = await _provision(session, workspace_name=workspace_name, owner=user)

    issued = await CredentialService(session).issue(user=user, purpose=TokenPurpose.INVITE)
    await session.commit()

    return BootstrapResult(
        workspace_name=workspace.name,
        email=user.email,
        set_password_url=(f"{settings.app_base_url.rstrip('/')}/set-password?token={issued.token}"),
    )


async def _provision(session: AsyncSession, *, workspace_name: str, owner: User) -> Workspace:
    workspace, _membership = await WorkspaceProvisioner(session).provision(
        name=workspace_name, owner=owner
    )
    return workspace
