"""M8 — sales groups, assignment rules, scheduling.

Revision ID: 0008_m8_assignment
Revises: 0007_m7_work

Also widens the Root and Admin capability defaults. `admin_access` grants
everything in a group *including capabilities added later*, but only for groups
the template names at all — and Root and Admin named four of ten. M7 hit this
with `tasks`; M8's assignment endpoints sit behind `automations`, M9's reports
behind `reports`, and M10's API keys behind `automations` and `integrations`.

Root is `is_readonly`, so an admin cannot repair the omission from the settings
UI. Left alone, every one of those endpoints would 403 in every workspace
created before this migration, permanently. The backfill is idempotent and only
adds groups a template does not already name, so a workspace that has since
edited its Admin template keeps its edits.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_m8_assignment"
down_revision = "0007_m7_work"
branch_labels = None
depends_on = None

#: Groups Root must name. Admin gets the same minus billing — see
#: `services/provisioning.py` for why that one is opt-in.
_ROOT_GROUPS = (
    "reports",
    "automations",
    "calling",
    "salesform",
    "billings",
    "integrations",
)
_ADMIN_GROUPS = tuple(g for g in _ROOT_GROUPS if g != "billings")


def _backfill(groups: tuple[str, ...], names: tuple[str, ...]) -> None:
    for group in groups:
        op.execute(
            sa.text(
                """
                UPDATE permission_templates
                   SET capabilities = jsonb_set(
                           capabilities,
                           CAST(:path AS text[]),
                           '{"admin_access": true}'::jsonb,
                           true
                       )
                 WHERE name IN :names
                   AND NOT (capabilities ? :group)
                """
            ).bindparams(
                sa.bindparam("path", value="{" + group + "}", type_=sa.Text()),
                sa.bindparam("names", value=names, expanding=True),
                sa.bindparam("group", value=group, type_=sa.Text()),
            )
        )


def upgrade() -> None:
    assignment_strategy = postgresql.ENUM(
        "ROUND_ROBIN",
        "WEIGHTED",
        "FIELD_VALUE",
        "SALES_GROUP",
        "FIXED",
        "UNASSIGNED",
        name="assignment_strategy",
        create_type=False,
    )
    assignment_strategy.create(op.get_bind(), checkfirst=True)

    report_format = postgresql.ENUM(
        "CSV", "XLSX", name="scheduled_report_format", create_type=False
    )
    report_format.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "sales_groups",
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
        sa.Column("description", sa.Text()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "name", name="sales_groups_name_uq"),
    )
    op.create_index("ix_sales_groups_workspace_id", "sales_groups", ["workspace_id"])

    op.create_table(
        "sales_group_members",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sales_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "membership_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memberships.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("weight >= 1 AND weight <= 100", name="sales_group_weight_range"),
    )
    op.create_index("ix_sales_group_members_workspace_id", "sales_group_members", ["workspace_id"])

    op.create_table(
        "assignment_rules",
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
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "conditions", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("strategy", assignment_strategy, nullable=False),
        sa.Column(
            "config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("skip_unavailable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "name", name="assignment_rules_name_uq"),
    )
    op.create_index(
        "ix_assignment_rules_order", "assignment_rules", ["workspace_id", "priority", "id"]
    )
    op.create_index("ix_assignment_rules_workspace_id", "assignment_rules", ["workspace_id"])

    op.create_table(
        "assignment_cursors",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assignment_rules.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "last_membership_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memberships.id", ondelete="SET NULL"),
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_assignment_cursors_workspace_id", "assignment_cursors", ["workspace_id"])

    op.create_table(
        "scheduled_reports",
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
        sa.Column("report_type", sa.String(length=40), nullable=False),
        sa.Column(
            "params", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("cron", sa.String(length=120), nullable=False),
        sa.Column("recipients", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("format", report_format, nullable=False, server_default="CSV"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memberships.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "name", name="scheduled_reports_name_uq"),
    )
    op.create_index(
        "ix_scheduled_reports_active", "scheduled_reports", ["workspace_id", "is_active"]
    )
    op.create_index("ix_scheduled_reports_workspace_id", "scheduled_reports", ["workspace_id"])

    _backfill(_ROOT_GROUPS, ("Root",))
    _backfill(_ADMIN_GROUPS, ("Admin",))


def downgrade() -> None:
    for group in _ROOT_GROUPS:
        op.execute(
            sa.text(
                "UPDATE permission_templates SET capabilities = capabilities - :group "
                "WHERE name IN ('Root', 'Admin')"
            ).bindparams(sa.bindparam("group", value=group, type_=sa.Text()))
        )

    op.drop_index("ix_scheduled_reports_workspace_id", table_name="scheduled_reports")
    op.drop_index("ix_scheduled_reports_active", table_name="scheduled_reports")
    op.drop_table("scheduled_reports")
    op.drop_index("ix_assignment_cursors_workspace_id", table_name="assignment_cursors")
    op.drop_table("assignment_cursors")
    op.drop_index("ix_assignment_rules_workspace_id", table_name="assignment_rules")
    op.drop_index("ix_assignment_rules_order", table_name="assignment_rules")
    op.drop_table("assignment_rules")
    op.drop_index("ix_sales_group_members_workspace_id", table_name="sales_group_members")
    op.drop_table("sales_group_members")
    op.drop_index("ix_sales_groups_workspace_id", table_name="sales_groups")
    op.drop_table("sales_groups")

    postgresql.ENUM(name="scheduled_report_format").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="assignment_strategy").drop(op.get_bind(), checkfirst=True)
