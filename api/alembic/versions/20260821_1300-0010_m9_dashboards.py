"""M9 — dashboards.

Revision ID: 0010_m9_dashboards
Revises: 0009_m10_intake

The last table in `docs/01-data-model.md` §5.5. `scheduled_reports` shares that
section but landed in M8, because a schedule needs a scheduler and a dashboard
does not.

Revision numbering runs 0008 → 0009 (M10) → 0010 (M9) because M10 was built
first: the event bus is what the planned voice integration attaches to. Alembic
cares about the chain, not the milestone numbers, and renumbering an applied
revision is exactly what CLAUDE.md forbids.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_m9_dashboards"
down_revision = "0009_m10_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
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
        # Null means shared with the workspace.
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memberships.id", ondelete="CASCADE"),
        ),
        # Non-null binds it to a permission template.
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("permission_templates.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "layout", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "name", "owner_id", name="dashboards_name_uq"),
    )
    op.create_index("ix_dashboards_workspace_id", "dashboards", ["workspace_id"])
    op.create_index("ix_dashboards_owner", "dashboards", ["workspace_id", "owner_id"])
    op.create_index("ix_dashboards_template", "dashboards", ["workspace_id", "template_id"])

    # The Caller template's View group has always promised a personal dashboard
    # (`show_personal_dashboard`), while its Access groups named no `reports`
    # capability at all — so every dashboard endpoint would 403 for a caller in
    # every workspace created before now. Nothing noticed until M9, because
    # nothing was gated on `reports` before it.
    #
    # Grants `view_reports` only: their own numbers, confined by the same
    # visibility rule as every list endpoint. Not team reports, not the
    # leaderboard. Idempotent, and skips a template that already names the
    # group, so an edited Caller keeps its edits.
    op.execute(
        """
        UPDATE permission_templates
           SET capabilities = jsonb_set(
                   capabilities,
                   '{reports}',
                   '{"view_reports": true}'::jsonb,
                   true
               )
         WHERE name = 'Caller'
           AND NOT (capabilities ? 'reports')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE permission_templates
           SET capabilities = capabilities - 'reports'
         WHERE name = 'Caller'
        """
    )

    op.drop_index("ix_dashboards_template", table_name="dashboards")
    op.drop_index("ix_dashboards_owner", table_name="dashboards")
    op.drop_index("ix_dashboards_workspace_id", table_name="dashboards")
    op.drop_table("dashboards")
