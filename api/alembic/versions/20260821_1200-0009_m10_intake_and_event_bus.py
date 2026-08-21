"""M10 — API keys, webhooks, the outbox and the intake log.

Revision ID: 0009_m10_intake
Revises: 0008_m8_assignment

Landing before M9 deliberately: the event bus is the seam the planned voice-agent
integration attaches to (`docs/06-voice-integration-contract.md` §2), and it is
the shorter of the two remaining milestones. M9's dashboards depend on nothing
here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_m10_intake"
down_revision = "0008_m8_assignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    outbox_status = postgresql.ENUM(
        "PENDING",
        "DELIVERING",
        "DELIVERED",
        "FAILED",
        "DEAD",
        name="outbox_status",
        create_type=False,
    )
    outbox_status.create(op.get_bind(), checkfirst=True)

    intake_outcome = postgresql.ENUM(
        "CREATED",
        "UPDATED",
        "SKIPPED",
        "REJECTED",
        name="intake_outcome",
        create_type=False,
    )
    intake_outcome.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("hashed_key", sa.Text(), nullable=False),
        sa.Column(
            "permission_template_id",
            postgresql.UUID(as_uuid=True),
            # RESTRICT, not CASCADE: deleting a template that a live integration
            # authenticates against should fail loudly rather than silently
            # revoking the key at 3am.
            sa.ForeignKey("permission_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memberships.id", ondelete="SET NULL"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "name", name="api_keys_name_uq"),
    )
    op.create_index("ix_api_keys_workspace_id", "api_keys", ["workspace_id"])
    # The lookup that avoids hashing every row on every request.
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])

    op.create_table(
        "webhook_endpoints",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column(
            "events",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "permission_template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("permission_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "name", name="webhook_endpoints_name_uq"),
    )
    op.create_index("ix_webhook_endpoints_workspace_id", "webhook_endpoints", ["workspace_id"])

    op.create_table(
        "outbox_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(length=60), nullable=False),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "endpoint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("status", outbox_status, nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_status_code", sa.Integer()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_outbox_events_workspace_id", "outbox_events", ["workspace_id"])
    # The dispatcher's only query. Partial, because DELIVERED and DEAD rows are
    # the majority in a healthy system and never appear in it.
    op.create_index(
        "ix_outbox_due",
        "outbox_events",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'FAILED')"),
    )
    op.create_index("ix_outbox_workspace_created", "outbox_events", ["workspace_id", "occurred_at"])

    op.create_table(
        "intake_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
        ),
        sa.Column("endpoint", sa.String(length=40), nullable=False),
        sa.Column("outcome", intake_outcome, nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column(
            "request_body",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "warnings",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="SET NULL")
        ),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_intake_log_workspace_id", "intake_log", ["workspace_id"])
    op.create_index("ix_intake_log_recent", "intake_log", ["workspace_id", "created_at"])
    op.create_index(
        "ix_intake_log_outcome", "intake_log", ["workspace_id", "outcome", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_intake_log_outcome", table_name="intake_log")
    op.drop_index("ix_intake_log_recent", table_name="intake_log")
    op.drop_index("ix_intake_log_workspace_id", table_name="intake_log")
    op.drop_table("intake_log")

    op.drop_index("ix_outbox_workspace_created", table_name="outbox_events")
    op.drop_index("ix_outbox_due", table_name="outbox_events")
    op.drop_index("ix_outbox_events_workspace_id", table_name="outbox_events")
    op.drop_table("outbox_events")

    op.drop_index("ix_webhook_endpoints_workspace_id", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")

    op.drop_index("ix_api_keys_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_workspace_id", table_name="api_keys")
    op.drop_table("api_keys")

    postgresql.ENUM(name="intake_outcome").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="outbox_status").drop(op.get_bind(), checkfirst=True)
