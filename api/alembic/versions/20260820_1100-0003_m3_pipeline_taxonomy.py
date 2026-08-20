"""M3 - pipeline and taxonomy settings.

Three tables of pure customer vocabulary: `stages`, `lost_reasons` and
`call_dispositions`. The product supplies the structure; every label in them is
a row a workspace owns and renames.

Two partial unique indexes carry rules the application cannot enforce safely on
its own, because two concurrent requests can each pass a SELECT check and both
insert:

- `stages_singleton_uq`      - one live INITIAL, WON and LOST per workspace
- `call_dispositions_default_uq` - exactly one live default disposition

Revises: 0002_m2_fields
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_m3_pipeline"
down_revision = "0002_m2_fields"


def upgrade() -> None:
    op.create_table(
        "call_dispositions",
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_archived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_call_dispositions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_dispositions")),
    )
    op.create_index(
        "call_dispositions_default_uq",
        "call_dispositions",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND is_archived = false"),
    )
    op.create_index(
        op.f("ix_call_dispositions_workspace_id"),
        "call_dispositions",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_call_dispositions_workspace_sort",
        "call_dispositions",
        ["workspace_id", "sort_order"],
        unique=False,
    )
    op.create_table(
        "lost_reasons",
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_lost_reasons_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lost_reasons")),
    )
    op.create_index(
        op.f("ix_lost_reasons_workspace_id"), "lost_reasons", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_lost_reasons_workspace_sort",
        "lost_reasons",
        ["workspace_id", "sort_order"],
        unique=False,
    )
    op.create_table(
        "stages",
        sa.Column(
            "kind", sa.Enum("INITIAL", "ACTIVE", "WON", "LOST", name="stage_kind"), nullable=False
        ),
        sa.Column("label", sa.String(length=28), nullable=False),
        sa.Column(
            "color", sa.String(length=9), server_default=sa.text("'#6b7280'"), nullable=False
        ),
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
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_stages_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stages")),
    )
    op.create_index(op.f("ix_stages_workspace_id"), "stages", ["workspace_id"], unique=False)
    op.create_index(
        "ix_stages_workspace_sort", "stages", ["workspace_id", "sort_order"], unique=False
    )
    op.create_index(
        "stages_singleton_uq",
        "stages",
        ["workspace_id", "kind"],
        unique=True,
        postgresql_where=sa.text("kind IN ('INITIAL', 'WON', 'LOST') AND is_archived = false"),
    )


def downgrade() -> None:
    op.drop_index(
        "stages_singleton_uq",
        table_name="stages",
        postgresql_where=sa.text("kind IN ('INITIAL', 'WON', 'LOST') AND is_archived = false"),
    )
    op.drop_index("ix_stages_workspace_sort", table_name="stages")
    op.drop_index(op.f("ix_stages_workspace_id"), table_name="stages")
    op.drop_table("stages")
    op.drop_index("ix_lost_reasons_workspace_sort", table_name="lost_reasons")
    op.drop_index(op.f("ix_lost_reasons_workspace_id"), table_name="lost_reasons")
    op.drop_table("lost_reasons")
    op.drop_index("ix_call_dispositions_workspace_sort", table_name="call_dispositions")
    op.drop_index(op.f("ix_call_dispositions_workspace_id"), table_name="call_dispositions")
    op.drop_index(
        "call_dispositions_default_uq",
        table_name="call_dispositions",
        postgresql_where=sa.text("is_default = true AND is_archived = false"),
    )
    op.drop_table("call_dispositions")
    op.execute("DROP TYPE IF EXISTS stage_kind")
