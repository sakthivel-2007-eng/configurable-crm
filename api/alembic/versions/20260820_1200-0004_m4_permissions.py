"""M4 - field-level permissions.

`template_field_grants` is the field matrix from docs/03-configuration-model.md
§6.4: one row per (template, field, grant) across VIEW / EDIT / IMPORT / EXPORT.
Presence means granted, absence means denied - there is no explicit deny, which
is what makes the matrix auditable. Keyed on the triple rather than a surrogate
id, because the row *is* the fact.

`template_lead_views` is "Set up your lead view" (§6.3): the per-template
lead-detail layout naming which fields appear, in what order, and which groups
start collapsed.

Revises: 0003_m3_pipeline
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_m4_permissions"
down_revision = "0003_m3_pipeline"


def upgrade() -> None:
    op.create_table(
        "template_field_grants",
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("field_id", sa.UUID(), nullable=False),
        sa.Column(
            "grant",
            sa.Enum("VIEW", "EDIT", "IMPORT", "EXPORT", name="permission_grant"),
            nullable=False,
        ),
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
            name=op.f("fk_template_field_grants_field_id_lead_fields"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["permission_templates.id"],
            name=op.f("fk_template_field_grants_template_id_permission_templates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_template_field_grants_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "template_id", "field_id", "grant", name="pk_template_field_grants"
        ),
    )
    op.create_index(
        op.f("ix_template_field_grants_workspace_id"),
        "template_field_grants",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "tfg_template_idx", "template_field_grants", ["template_id", "grant"], unique=False
    )
    op.create_table(
        "template_lead_views",
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column(
            "layout",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
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
            ["template_id"],
            ["permission_templates.id"],
            name=op.f("fk_template_lead_views_template_id_permission_templates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_template_lead_views_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_lead_views")),
        sa.UniqueConstraint("template_id", name="uq_template_lead_views_template_id"),
    )
    op.create_index(
        op.f("ix_template_lead_views_workspace_id"),
        "template_lead_views",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_template_lead_views_workspace_id"), table_name="template_lead_views")
    op.drop_table("template_lead_views")
    op.drop_index("tfg_template_idx", table_name="template_field_grants")
    op.drop_index(op.f("ix_template_field_grants_workspace_id"), table_name="template_field_grants")
    op.drop_table("template_field_grants")
    op.execute("DROP TYPE IF EXISTS permission_grant")
