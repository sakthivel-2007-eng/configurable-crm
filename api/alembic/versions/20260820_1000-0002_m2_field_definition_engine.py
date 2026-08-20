"""M2 — the field definition engine.

Creates the configurable schema itself: `lead_fields` and `field_options` for
the lead schema, `custom_action_types` / `action_fields` /
`action_field_options` for per-action forms, and `indexed_fields` for the
workspace-declared expression indexes.

`custom_action_types` lands here rather than in M3 because `action_fields`
holds a foreign key to it — the cluster has to be created together. M3 adds its
behaviour, not its table.

Three enum types arrive with this revision: `lead_field_type` (13 values),
`action_field_type` (8) and `action_direction` (3). They are product concepts,
not customer taxonomy — see docs/01-data-model.md §1.

Revises: 0001_m1_tenancy
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_m2_fields"
down_revision = "0001_m1_tenancy"


def upgrade() -> None:
    op.create_table(
        "custom_action_types",
        sa.Column("code", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("icon", sa.String(length=64), nullable=True),
        sa.Column("score", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("INBOUND", "OUTBOUND", "INFORMATION", name="action_direction"),
            server_default=sa.text("'INFORMATION'::action_direction"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("allow_predated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.CheckConstraint(
            "score BETWEEN -1000 AND 1000",
            name=op.f("ck_custom_action_types_ck_custom_action_types_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_custom_action_types_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_action_types")),
        sa.UniqueConstraint(
            "workspace_id", "code", name="uq_custom_action_types_workspace_id_code"
        ),
    )
    op.create_index(
        op.f("ix_custom_action_types_workspace_id"),
        "custom_action_types",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "lead_fields",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column(
            "field_type",
            sa.Enum(
                "TEXT",
                "DROPDOWN",
                "TAGS",
                "EMAIL",
                "PHONE",
                "CHECKBOX",
                "DATE",
                "MONEY",
                "NUMBER",
                "WEBSITE",
                "DEPENDENT_DROPDOWN",
                "RECURRING_DATE",
                "LOCATION",
                name="lead_field_type",
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("field_group", sa.String(length=80), nullable=True),
        sa.Column("show_in_import", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "show_in_quick_add", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "lock_after_create", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "can_use_variable", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_lead_fields_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lead_fields")),
        sa.UniqueConstraint("workspace_id", "key", name="uq_lead_fields_workspace_id_key"),
    )
    op.create_index(
        op.f("ix_lead_fields_workspace_id"), "lead_fields", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_lead_fields_workspace_sort", "lead_fields", ["workspace_id", "sort_order"], unique=False
    )
    op.create_table(
        "action_fields",
        sa.Column("action_type_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column(
            "field_type",
            sa.Enum(
                "TEXT",
                "NUMBER",
                "DATE",
                "DROPDOWN",
                "TAGS",
                "USER",
                "FILE",
                "MEDIA_LINK",
                name="action_field_type",
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
            name=op.f("fk_action_fields_action_type_id_custom_action_types"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_action_fields_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_fields")),
        sa.UniqueConstraint("action_type_id", "key", name="uq_action_fields_action_type_id_key"),
    )
    op.create_index(
        "ix_action_fields_type_sort",
        "action_fields",
        ["action_type_id", "sort_order"],
        unique=False,
    )
    op.create_index(
        op.f("ix_action_fields_workspace_id"), "action_fields", ["workspace_id"], unique=False
    )
    op.create_table(
        "field_options",
        sa.Column("field_id", sa.UUID(), nullable=False),
        sa.Column("parent_option_id", sa.UUID(), nullable=True),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("label", sa.String(length=70), nullable=False),
        sa.Column("color", sa.String(length=9), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
            ["field_id"],
            ["lead_fields.id"],
            name=op.f("fk_field_options_field_id_lead_fields"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_option_id"],
            ["field_options.id"],
            name=op.f("fk_field_options_parent_option_id_field_options"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_field_options_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_field_options")),
        sa.UniqueConstraint("field_id", "code", name="uq_field_options_field_id_code"),
    )
    op.create_index(
        "ix_field_options_field_sort", "field_options", ["field_id", "sort_order"], unique=False
    )
    op.create_index("ix_field_options_parent", "field_options", ["parent_option_id"], unique=False)
    op.create_index(
        op.f("ix_field_options_workspace_id"), "field_options", ["workspace_id"], unique=False
    )
    op.create_table(
        "indexed_fields",
        sa.Column("field_id", sa.UUID(), nullable=False),
        sa.Column("index_name", sa.String(length=63), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            ["field_id"],
            ["lead_fields.id"],
            name=op.f("fk_indexed_fields_field_id_lead_fields"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_indexed_fields_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_indexed_fields")),
        sa.UniqueConstraint(
            "workspace_id", "field_id", name="uq_indexed_fields_workspace_id_field_id"
        ),
    )
    op.create_index(
        op.f("ix_indexed_fields_workspace_id"), "indexed_fields", ["workspace_id"], unique=False
    )
    op.create_table(
        "action_field_options",
        sa.Column("action_field_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("label", sa.String(length=70), nullable=False),
        sa.Column("color", sa.String(length=9), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
            ["action_field_id"],
            ["action_fields.id"],
            name=op.f("fk_action_field_options_action_field_id_action_fields"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_action_field_options_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_field_options")),
        sa.UniqueConstraint(
            "action_field_id", "code", name="uq_action_field_options_action_field_id_code"
        ),
    )
    op.create_index(
        op.f("ix_action_field_options_workspace_id"),
        "action_field_options",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_action_field_options_workspace_id"), table_name="action_field_options")
    op.drop_table("action_field_options")
    op.drop_index(op.f("ix_indexed_fields_workspace_id"), table_name="indexed_fields")
    op.drop_table("indexed_fields")
    op.drop_index(op.f("ix_field_options_workspace_id"), table_name="field_options")
    op.drop_index("ix_field_options_parent", table_name="field_options")
    op.drop_index("ix_field_options_field_sort", table_name="field_options")
    op.drop_table("field_options")
    op.drop_index(op.f("ix_action_fields_workspace_id"), table_name="action_fields")
    op.drop_index("ix_action_fields_type_sort", table_name="action_fields")
    op.drop_table("action_fields")
    op.drop_index("ix_lead_fields_workspace_sort", table_name="lead_fields")
    op.drop_index(op.f("ix_lead_fields_workspace_id"), table_name="lead_fields")
    op.drop_table("lead_fields")
    op.drop_index(op.f("ix_custom_action_types_workspace_id"), table_name="custom_action_types")
    op.drop_table("custom_action_types")
    # The three enum types this revision introduced. Dropped after the tables
    # that reference them, IF EXISTS so a partial downgrade is still reversible.
    op.execute("DROP TYPE IF EXISTS action_field_type")
    op.execute("DROP TYPE IF EXISTS lead_field_type")
    op.execute("DROP TYPE IF EXISTS action_direction")
