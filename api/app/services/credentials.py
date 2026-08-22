"""Issuing and redeeming password-set tokens (M11).

This is how every account in the product comes to have a password. The workspace
model is invite-only: there is no registration endpoint, so an invitation and a
forgotten-password reset are the same act — prove control of an address, choose
a credential — and they share one implementation.

Five properties, each of which is the whole point of the code around it:

**The token is stored hashed.** It is a bearer credential for the account. A
readable copy in the database would let anyone holding a dump become any user,
which is the same reason refresh tokens are stored as SHA-256 here.

**Requesting a reset never reveals whether an address exists.** The endpoint
answers identically either way. Anything else turns the login page into a
membership oracle for whoever is probing it — and for a B2B CRM, "does this
company use this product" is itself worth money.

**A token is single-use, and the row survives redemption.** "Already spent" and
"no such token" are different facts, and only the first is evidence of a replay.

**Redeeming one revokes every refresh token the user holds.** Somebody resetting
a password is very often somebody who thinks their account is compromised.
Leaving live sessions running would make the reset theatre.

**An invitation lives longer than a reset.** An invite has to survive a weekend
in an inbox. A reset the person requested thirty seconds ago does not, and a
long window on unrequested mail is a long window for a leaked mailbox.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import api_error
from app.models import PasswordResetToken, RefreshToken, User

__all__ = [
    "INVITE_TTL",
    "RESET_TTL",
    "CredentialService",
    "IssuedResetToken",
    "TokenPurpose",
]

#: Long enough to survive a weekend in an inbox.
INVITE_TTL = dt.timedelta(days=7)
#: Short, because the person asked for it moments ago.
RESET_TTL = dt.timedelta(hours=1)

#: 32 bytes of urlsafe randomness — guessing is not a strategy.
_TOKEN_BYTES = 32


class TokenPurpose:
    INVITE = "INVITE"
    RESET = "RESET"


@dataclass(frozen=True, slots=True)
class IssuedResetToken:
    """The plaintext, returned once so it can be mailed and then forgotten."""

    token: str
    expires_at: dt.datetime
    purpose: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class CredentialService:
    """Password-set tokens, for both invitations and resets."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def issue(
        self, *, user: User, purpose: str, now: dt.datetime | None = None
    ) -> IssuedResetToken:
        """Mint a token for `user`, superseding any outstanding one.

        Superseding matters: two live tokens mean two chances for an old email
        to still work after the newer one was acted on.
        """
        moment = now or dt.datetime.now(dt.UTC)
        ttl = INVITE_TTL if purpose == TokenPurpose.INVITE else RESET_TTL

        await self._session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=moment)
        )

        token = secrets.token_urlsafe(_TOKEN_BYTES)
        self._session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_hash(token),
                purpose=purpose,
                expires_at=moment + ttl,
            )
        )
        await self._session.flush()
        return IssuedResetToken(token=token, expires_at=moment + ttl, purpose=purpose)

    async def find_user(self, email: str) -> User | None:
        """The user for an address, or None. Callers must not leak which."""
        rows = await self._session.execute(
            select(User).where(User.email == email.casefold()).limit(1)
        )
        return rows.scalar_one_or_none()

    async def redeem(
        self, *, token: str, password_hash: str, now: dt.datetime | None = None
    ) -> User:
        """Spend a token and set the password. Raises on anything wrong.

        Every failure answers the same way. Distinguishing "expired" from
        "already used" from "never existed" would tell somebody working through
        a list of guesses which ones were once real.
        """
        moment = now or dt.datetime.now(dt.UTC)

        rows = await self._session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash(token))
        )
        record = rows.scalar_one_or_none()

        if record is None or record.used_at is not None or record.expires_at <= moment:
            raise api_error(
                400,
                "invalid_token",
                "That link is not valid. It may have expired or already been used — "
                "ask for a new one.",
            )

        user = await self._session.get(User, record.user_id)
        if user is None or not user.is_active:  # pragma: no cover - FK guarantees
            raise api_error(400, "invalid_token", "That link is not valid.")

        user.password_hash = password_hash
        record.used_at = moment

        # Every live session goes. Someone resetting a password is very often
        # someone who believes their account is compromised, and leaving their
        # other sessions running would make the reset theatre.
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=moment)
        )
        await self._session.flush()
        return user

    async def purge_expired(self, *, now: dt.datetime | None = None) -> int:
        """Drop tokens long past use. Called by the scheduler tick.

        Kept for a grace period after expiry rather than deleted on the dot, so
        "this token was spent" stays answerable for a while after the fact.
        """
        moment = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=30)
        result = await self._session.execute(
            delete(PasswordResetToken).where(PasswordResetToken.expires_at < moment)
        )
        # `rowcount` is on the DBAPI cursor result, which `Result` exposes for
        # DML but does not type.
        return int(getattr(result, "rowcount", 0) or 0)
