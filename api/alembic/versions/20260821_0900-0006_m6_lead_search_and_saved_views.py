"""M6 - lead search, saved filters and table layouts.

Three things the lead list needs and M5 did not have:

1. `leads.search_vector` plus its GIN index, and the trigram index on
   `identity_value`. `docs/01-data-model.md` §4 specifies both; M5 shipped
   neither, so this revision owns them rather than editing an applied one.
   The two are complementary: a tsquery matches whole lexemes, so partial
   matching on a phone number goes through trigrams instead.
2. `saved_filters` - a filter DSL document plus who it is for (§6).
3. `table_layouts` - one member's columns for one filter.

The vector is a plain column maintained by the application, not a generated
column or a trigger. Which fields are searchable is decided by the type
registry, which is Python; a trigger would have to restate that in PL/pgSQL and
would drift from it the first time a type was added.

`pg_trgm` is created in 0001, so `gin_trgm_ops` is available here.

Revises: 0005_m5_leads
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_m6_filters"
down_revision = "0005_m5_leads"


def upgrade() -> None:
    op.create_table(
        "saved_filters",
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "definition",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "visibility",
            sa.Enum("PERSONAL", "SHARED", "ROLE", name="saved_filter_visibility"),
            server_default=sa.text("'PERSONAL'"),
            nullable=False,
        ),
        sa.Column("template_id", sa.UUID(), nullable=True),
        sa.Column("owner_membership_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint(
            "(visibility = 'ROLE') = (template_id IS NOT NULL)",
            name=op.f("ck_saved_filters_role_names_a_template"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_membership_id"],
            ["memberships.id"],
            name=op.f("fk_saved_filters_owner_membership_id_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["permission_templates.id"],
            name=op.f("fk_saved_filters_template_id_permission_templates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_saved_filters_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_filters")),
    )
    op.create_index(
        op.f("ix_saved_filters_workspace_id"), "saved_filters", ["workspace_id"], unique=False
    )
    op.create_index(
        "saved_filters_ws_owner_idx",
        "saved_filters",
        ["workspace_id", "owner_membership_id"],
        unique=False,
        postgresql_where=sa.text("is_archived = false"),
    )
    op.create_index(
        "saved_filters_ws_visibility_idx",
        "saved_filters",
        ["workspace_id", "visibility"],
        unique=False,
        postgresql_where=sa.text("is_archived = false"),
    )

    op.create_table(
        "table_layouts",
        sa.Column("membership_id", sa.UUID(), nullable=False),
        sa.Column("filter_id", sa.UUID(), nullable=True),
        sa.Column(
            "columns",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "column_widths",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("sort_key", sa.String(length=80), nullable=True),
        sa.Column("sort_desc", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            ["filter_id"],
            ["saved_filters.id"],
            name=op.f("fk_table_layouts_filter_id_saved_filters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["memberships.id"],
            name=op.f("fk_table_layouts_membership_id_memberships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_table_layouts_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_table_layouts")),
    )
    op.create_index(
        op.f("ix_table_layouts_workspace_id"), "table_layouts", ["workspace_id"], unique=False
    )
    # Two partial indexes rather than one constraint: `filter_id` is nullable
    # and NULLs do not collide in a unique index, so without the second one a
    # member could accumulate any number of default layouts.
    op.create_index(
        "table_layouts_member_default_uq",
        "table_layouts",
        ["workspace_id", "membership_id"],
        unique=True,
        postgresql_where=sa.text("filter_id IS NULL"),
    )
    op.create_index(
        "table_layouts_member_filter_uq",
        "table_layouts",
        ["workspace_id", "membership_id", "filter_id"],
        unique=True,
        postgresql_where=sa.text("filter_id IS NOT NULL"),
    )

    op.add_column("leads", sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True))
    op.create_index(
        "leads_search_idx", "leads", ["search_vector"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        "leads_identity_trgm",
        "leads",
        ["identity_value"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"identity_value": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "leads_identity_trgm",
        table_name="leads",
        postgresql_using="gin",
        postgresql_ops={"identity_value": "gin_trgm_ops"},
    )
    op.drop_index("leads_search_idx", table_name="leads", postgresql_using="gin")
    op.drop_column("leads", "search_vector")

    op.drop_index(
        "table_layouts_member_filter_uq",
        table_name="table_layouts",
        postgresql_where=sa.text("filter_id IS NOT NULL"),
    )
    op.drop_index(
        "table_layouts_member_default_uq",
        table_name="table_layouts",
        postgresql_where=sa.text("filter_id IS NULL"),
    )
    op.drop_index(op.f("ix_table_layouts_workspace_id"), table_name="table_layouts")
    op.drop_table("table_layouts")

    op.drop_index(
        "saved_filters_ws_visibility_idx",
        table_name="saved_filters",
        postgresql_where=sa.text("is_archived = false"),
    )
    op.drop_index(
        "saved_filters_ws_owner_idx",
        table_name="saved_filters",
        postgresql_where=sa.text("is_archived = false"),
    )
    op.drop_index(op.f("ix_saved_filters_workspace_id"), table_name="saved_filters")
    op.drop_table("saved_filters")
    # The table is gone but the enum type is not — Postgres keeps it, and a
    # re-upgrade would fail on "type already exists".
    op.execute("DROP TYPE IF EXISTS saved_filter_visibility")
