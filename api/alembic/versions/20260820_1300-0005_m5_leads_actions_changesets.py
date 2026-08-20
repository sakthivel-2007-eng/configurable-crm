"""M5 - leads, actions and changesets.

The three things PROMPTS.md M5 says cannot be retrofitted, all landing here:

1. `changesets`, with every action carrying `changeset_id` - what makes M7's
   undo possible.
2. `actions_status_change_idx` and `actions_assignment_idx`, the expression
   indexes over the STAGE_CHANGE / ASSIGNMENT_CHANGE payloads that M6's history
   filters need. Built in the milestone that defines the payloads, not the one
   that queries them.
3. `actions.score_applied`, snapshotted per action so editing a custom action
   type's score later does not rewrite history.

Lead values are JSONB keyed by `lead_fields.key` - no per-customer columns, and
no DDL at runtime beyond the indexed-field worker.

Revises: 0004_m4_permissions
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_m5_leads"
down_revision = "0004_m4_permissions"


def upgrade() -> None:
    op.create_table(
        "changesets",
        sa.Column(
            "source",
            sa.Enum(
                "SINGLE_EDIT",
                "BULK_EDIT",
                "IMPORT",
                "DISTRIBUTION",
                "AUTOMATION",
                "INTAKE",
                name="changeset_source",
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("lead_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_undone", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_by_id", sa.UUID(), nullable=True),
        sa.Column("undo_of_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["memberships.id"],
            name=op.f("fk_changesets_actor_id_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["undo_of_id"],
            ["changesets.id"],
            name=op.f("fk_changesets_undo_of_id_changesets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["undone_by_id"],
            ["memberships.id"],
            name=op.f("fk_changesets_undone_by_id_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_changesets_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_changesets")),
    )
    op.create_index(
        "changesets_ws_created_idx", "changesets", ["workspace_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_changesets_workspace_id"), "changesets", ["workspace_id"], unique=False
    )
    op.create_table(
        "leads",
        sa.Column("identity_value", sa.String(length=320), nullable=False),
        sa.Column(
            "values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("stage_id", sa.UUID(), nullable=True),
        sa.Column("lost_reason_id", sa.UUID(), nullable=True),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column("score", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rating IS NULL OR rating BETWEEN 1 AND 5", name=op.f("ck_leads_ck_leads_rating_range")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["memberships.id"],
            name=op.f("fk_leads_assignee_id_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["memberships.id"],
            name=op.f("fk_leads_created_by_id_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lost_reason_id"],
            ["lost_reasons.id"],
            name=op.f("fk_leads_lost_reason_id_lost_reasons"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_leads_stage_id_stages"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_leads_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
    )
    op.create_index(op.f("ix_leads_workspace_id"), "leads", ["workspace_id"], unique=False)
    op.create_index(
        "leads_identity_uq",
        "leads",
        ["workspace_id", "identity_value"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("leads_values_gin", "leads", ["values"], unique=False, postgresql_using="gin")
    op.create_index(
        "leads_ws_assignee_idx",
        "leads",
        ["workspace_id", "assignee_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "leads_ws_created_idx",
        "leads",
        ["workspace_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "leads_ws_score_idx",
        "leads",
        ["workspace_id", "score"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "leads_ws_stage_idx",
        "leads",
        ["workspace_id", "stage_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "message_templates",
        sa.Column(
            "channel", sa.Enum("WHATSAPP", "SMS", "EMAIL", name="template_channel"), nullable=False
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("template_id", sa.UUID(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["memberships.id"],
            name=op.f("fk_message_templates_owner_id_memberships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["permission_templates.id"],
            name=op.f("fk_message_templates_template_id_permission_templates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_message_templates_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_templates")),
    )
    op.create_index(
        op.f("ix_message_templates_workspace_id"),
        "message_templates",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_message_templates_ws_channel",
        "message_templates",
        ["workspace_id", "channel"],
        unique=False,
    )
    op.create_table(
        "actions",
        sa.Column("lead_id", sa.UUID(), nullable=False),
        sa.Column("changeset_id", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "LEAD_CREATED",
                "FIELD_CHANGE",
                "STAGE_CHANGE",
                "ASSIGNMENT_CHANGE",
                "RATING_CHANGE",
                "NOTE",
                "CALL_LOGGED",
                "WHATSAPP_SENT",
                "EMAIL_SENT",
                "SMS_SENT",
                "TASK_CREATED",
                "TASK_COMPLETED",
                "CUSTOM",
                name="system_action_kind",
            ),
            nullable=False,
        ),
        sa.Column("action_type_id", sa.UUID(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("score_applied", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "performed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["action_type_id"],
            ["custom_action_types.id"],
            name=op.f("fk_actions_action_type_id_custom_action_types"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["memberships.id"],
            name=op.f("fk_actions_actor_id_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["changeset_id"],
            ["changesets.id"],
            name=op.f("fk_actions_changeset_id_changesets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name=op.f("fk_actions_lead_id_leads"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_actions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actions")),
    )
    op.create_index(
        "actions_assignment_idx",
        "actions",
        [
            "workspace_id",
            sa.literal_column("(payload ->> 'old_assignee_id')"),
            sa.literal_column("(payload ->> 'new_assignee_id')"),
            "performed_at",
        ],
        unique=False,
        postgresql_where=sa.text("kind = 'ASSIGNMENT_CHANGE'"),
    )
    op.create_index("actions_changeset_idx", "actions", ["changeset_id"], unique=False)
    op.create_index("actions_lead_time_idx", "actions", ["lead_id", "performed_at"], unique=False)
    op.create_index(
        "actions_status_change_idx",
        "actions",
        [
            "workspace_id",
            sa.literal_column("(payload ->> 'old_stage_id')"),
            sa.literal_column("(payload ->> 'new_stage_id')"),
            "performed_at",
        ],
        unique=False,
        postgresql_where=sa.text("kind = 'STAGE_CHANGE'"),
    )
    op.create_index(
        "actions_ws_kind_idx", "actions", ["workspace_id", "kind", "performed_at"], unique=False
    )
    op.create_index(op.f("ix_actions_workspace_id"), "actions", ["workspace_id"], unique=False)
    op.create_table(
        "action_attachments",
        sa.Column("action_id", sa.UUID(), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["actions.id"],
            name=op.f("fk_action_attachments_action_id_actions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_action_attachments_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_attachments")),
    )
    op.create_index(
        op.f("ix_action_attachments_workspace_id"),
        "action_attachments",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_action_attachments_workspace_id"), table_name="action_attachments")
    op.drop_table("action_attachments")
    op.drop_index(op.f("ix_actions_workspace_id"), table_name="actions")
    op.drop_index("actions_ws_kind_idx", table_name="actions")
    op.drop_index(
        "actions_status_change_idx",
        table_name="actions",
        postgresql_where=sa.text("kind = 'STAGE_CHANGE'"),
    )
    op.drop_index("actions_lead_time_idx", table_name="actions")
    op.drop_index("actions_changeset_idx", table_name="actions")
    op.drop_index(
        "actions_assignment_idx",
        table_name="actions",
        postgresql_where=sa.text("kind = 'ASSIGNMENT_CHANGE'"),
    )
    op.drop_table("actions")
    op.drop_index("ix_message_templates_ws_channel", table_name="message_templates")
    op.drop_index(op.f("ix_message_templates_workspace_id"), table_name="message_templates")
    op.drop_table("message_templates")
    op.drop_index(
        "leads_ws_stage_idx", table_name="leads", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_index(
        "leads_ws_score_idx", table_name="leads", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_index(
        "leads_ws_created_idx", table_name="leads", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_index(
        "leads_ws_assignee_idx", table_name="leads", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_index("leads_values_gin", table_name="leads", postgresql_using="gin")
    op.drop_index(
        "leads_identity_uq", table_name="leads", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_index(op.f("ix_leads_workspace_id"), table_name="leads")
    op.drop_table("leads")
    op.drop_index(op.f("ix_changesets_workspace_id"), table_name="changesets")
    op.drop_index("changesets_ws_created_idx", table_name="changesets")
    op.drop_table("changesets")
    op.execute("DROP TYPE IF EXISTS template_channel")
    op.execute("DROP TYPE IF EXISTS system_action_kind")
    op.execute("DROP TYPE IF EXISTS changeset_source")
