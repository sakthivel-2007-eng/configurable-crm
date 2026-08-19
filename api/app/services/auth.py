"""Login, refresh rotation and logout.

Two rules shape everything here.

**Failed authentication says as little as possible.** Wrong email, wrong
password and unknown account all produce the same 401 with the same code, and
the unknown-account path burns a comparable amount of CPU so it cannot be timed
apart.

**Licence and activity are checked after the password, not before.** Otherwise
`403 no_license` on a wrong password would tell an attacker the address is real
and licensed. Both gates are reached only once the credentials are known good.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.passwords import PasswordHasherService
from app.auth.tokens import TokenService
from app.errors import forbidden, unauthorized
from app.models import Membership, RefreshToken, User

__all__ = ["AuthService", "TokenPair"]


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in_seconds: int
    token_type: str = "bearer"  # a scheme name, not a credential


class AuthService:
    """Credential verification and refresh-token lifecycle."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        tokens: TokenService,
        hasher: PasswordHasherService,
    ) -> None:
        self._session = session
        self._tokens = tokens
        self._hasher = hasher

    # --- login -------------------------------------------------------------

    async def authenticate(self, *, email: str, password: str) -> User:
        """Verify credentials and the account-level gates.

        Membership-level gates (`no_license`, `member_inactive`) are applied by
        `assert_can_access_any_workspace` once we know the password was right.
        """
        result = await self._session.execute(select(User).where(User.email == email).limit(1))
        user = result.scalar_one_or_none()

        if user is None:
            # Equalise timing so a nonexistent address is not measurably faster
            # to reject than a wrong password.
            self._hasher.dummy_verify()
            raise unauthorized("invalid_credentials", "Email or password is incorrect")

        if not self._hasher.verify(user.password_hash, password):
            raise unauthorized("invalid_credentials", "Email or password is incorrect")

        if not user.is_active:
            raise forbidden("member_inactive", "This account has been deactivated")

        if self._hasher.needs_rehash(user.password_hash):
            user.password_hash = self._hasher.hash(password)

        return user

    async def load_memberships(self, user: User) -> list[Membership]:
        result = await self._session.execute(
            select(Membership)
            .where(Membership.user_id == user.id)
            .options(
                selectinload(Membership.workspace),
                selectinload(Membership.template),
            )
            .order_by(Membership.created_at)
        )
        return list(result.scalars().all())

    @staticmethod
    def assert_can_access_any_workspace(memberships: list[Membership]) -> None:
        """Refuse a login that could not reach a single workspace.

        The distinction the API contract asks for: a member whose memberships
        are all deactivated gets `member_inactive`; one who is active everywhere
        but licensed nowhere gets `no_license`. Reporting "inactive" first is
        deliberate — it is the more actionable of the two for an admin reading
        a support ticket.
        """
        if not memberships:
            # A user with no membership at all is not an error state — they can
            # sign in and create a workspace.
            return

        if not any(membership.is_active for membership in memberships):
            raise forbidden("member_inactive", "All of your memberships have been deactivated")

        if not any(membership.is_active and membership.has_license for membership in memberships):
            raise forbidden("no_license", "No licensed membership is available for this account")

    # --- token issuing -----------------------------------------------------

    async def issue_tokens(self, user: User, *, family_id: uuid.UUID | None = None) -> TokenPair:
        access_token, expires_in = self._tokens.issue_access_token(user.id)
        refresh = self._tokens.issue_refresh_token(family_id=family_id)

        self._session.add(
            RefreshToken(
                user_id=user.id,
                family_id=refresh.family_id,
                token_hash=refresh.token_hash,
                expires_at=refresh.expires_at,
            )
        )

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh.token,
            expires_in_seconds=expires_in,
        )

    # --- refresh -----------------------------------------------------------

    async def rotate(self, presented_token: str) -> tuple[User, TokenPair]:
        """Exchange a refresh token for a new pair, revoking the old one.

        Replay of an already-rotated token means the token leaked, so the whole
        family is revoked rather than just the presented row. The legitimate
        holder is logged out too — that is the correct trade when we know one
        of the two parties is an attacker and cannot tell which.
        """
        token_hash = self._tokens.hash_refresh_token(presented_token)
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).limit(1)
        )
        stored = result.scalar_one_or_none()

        if stored is None:
            raise unauthorized("invalid_token", "The refresh token is invalid or has expired")

        now = datetime.now(UTC)

        if stored.revoked_at is not None:
            await self._revoke_family(stored.family_id)
            # Committed here, not left to the handler: we are about to raise,
            # and an exception unwinds the request without reaching its commit.
            # An un-persisted revocation is no revocation at all — the thief
            # would simply retry with the token they already hold.
            await self._session.commit()
            raise unauthorized("token_reuse_detected", "This session has been terminated")

        if stored.expires_at <= now:
            raise unauthorized("invalid_token", "The refresh token is invalid or has expired")

        user_result = await self._session.execute(
            select(User).where(User.id == stored.user_id).limit(1)
        )
        user = user_result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise unauthorized("invalid_token", "The refresh token is invalid or has expired")

        # Re-run the membership gates: a licence revoked mid-session must not
        # survive to the next 30-day refresh.
        memberships = await self.load_memberships(user)
        self.assert_can_access_any_workspace(memberships)

        stored.revoked_at = now
        pair = await self.issue_tokens(user, family_id=stored.family_id)
        return user, pair

    async def logout(self, presented_token: str) -> None:
        """Revoke the presented token's whole family.

        Logging out of one device should not leave a rotation chain alive that
        the same login started elsewhere.
        """
        token_hash = self._tokens.hash_refresh_token(presented_token)
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).limit(1)
        )
        stored = result.scalar_one_or_none()
        if stored is not None:
            await self._revoke_family(stored.family_id)

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        """Invalidate every session a user holds.

        Called on password change and on deactivation — both are moments where
        an existing session must stop working immediately.
        """
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )

    async def _revoke_family(self, family_id: uuid.UUID) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )

    # --- password ----------------------------------------------------------

    async def change_password(self, user: User, *, current: str, replacement: str) -> None:
        if not self._hasher.verify(user.password_hash, current):
            raise unauthorized("invalid_credentials", "Current password is incorrect")
        user.password_hash = self._hasher.hash(replacement)
        await self.revoke_all_for_user(user.id)
