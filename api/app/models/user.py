"""Users and refresh tokens.

Users are global to the deployment. Permissions attach to a `Membership` in a
workspace, never to a user — the same person can be an Admin in one workspace
and a Caller in another.

Refresh tokens are stored server-side so rotation can invalidate the previous
token in a family. Access tokens stay stateless JWTs; only refresh state hits
the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.mixins import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.workspace import Membership


class User(Base, TimestampMixin):
    """A person who can sign in.

    `email` is `citext`: authenticating one casing and duplicating another is a
    classic account-takeover-adjacent bug. `password_hash` holds an argon2id
    encoded string, which carries its own parameters — nothing else to store.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    email: Mapped[str] = mapped_column(CITEXT(), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class RefreshToken(Base):
    """One refresh token in a rotation family.

    A family is the chain of tokens descending from a single login. Presenting
    an already-rotated token means the token leaked and is being replayed, so
    the whole family is revoked rather than just that row. Only the SHA-256 of
    the token is stored.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set on rotation or logout. Rows are never deleted — the history is what
    # makes replay detection possible.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordResetToken(Base):
    """A single-use credential for setting a password (M11).

    Two flows share this table, because they are the same act — proving control
    of an address and choosing a password:

    - **an invitation**, which is how every account in this product is created;
      invite-only means there is no other route in
    - **a forgotten password**

    They differ only in how long the token lives. An invite has to survive a
    weekend in somebody's inbox; a reset the person just asked for does not, and
    a long window on an unrequested email is a long window for a leaked mailbox.

    Only the SHA-256 is stored, exactly as refresh tokens are. A reset token is
    a bearer credential for the account — a readable copy in the database would
    let anyone with a database dump become any user.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    #: INVITE or RESET — only the lifetime differs, but the audit answer to
    #: "how did this account come to exist" should not have to be inferred.
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Single use. Set on redemption; the row is kept because "this token was
    #: already spent" is a different answer from "no such token", and only the
    #: first is evidence of a replay.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_password_reset_active", "user_id", "expires_at"),)
