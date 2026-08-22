"""M11 — password-set tokens.

Revision ID: 0011_m11_credentials
Revises: 0010_m9_dashboards

The table that makes invitations usable. `invite_member` has always created an
account with an unguessable password and a docstring promising the invitee
"arrives through a password reset" — but no reset flow existed, so every invited
person held an account they could never sign in to.

The workspace model is invite-only, so this *is* the account-creation path.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_m11_credentials"
down_revision = "0010_m9_dashboards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SHA-256 only. A reset token is a bearer credential for the account;
        # a readable copy would let anyone with a dump become any user.
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Single use. The row survives redemption because "already spent" and
        # "no such token" are different facts, and only the first is evidence
        # of a replay.
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index("ix_password_reset_active", "password_reset_tokens", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_password_reset_active", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
