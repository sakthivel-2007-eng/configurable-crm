"""M7 - tasks, labels and import jobs.

`docs/01-data-model.md` §6 names `labels`, `lead_labels` and `tasks`. The job
table is M7's own: every import and export in the contract answers `{job_id}`,
and something has to be behind it.

`lead_labels` is keyed on `(lead_id, label_id)` rather than a surrogate id —
the row *is* the fact — which is the same reasoning `template_field_grants`
uses. It still carries `workspace_id`, so `ScopedSession`'s loader criteria
filters it like every other tenant table.

Revises: 0006_m6_filters

Create Date: 2026-08-21 10:13:04.894439

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_m7_work"
down_revision = "0006_m6_filters"


def upgrade() -> None:
    op.create_table(
        "labels",
        sa.Column("name", sa.String(length=60), nullable=False),
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
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_labels_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_labels")),
        sa.UniqueConstraint("workspace_id", "name", name="uq_labels_workspace_id_name"),
    )
    op.create_index(op.f("ix_labels_workspace_id"), "labels", ["workspace_id"], unique=False)
    op.create_table(
        "import_jobs",
        sa.Column(
            "kind",
            sa.Enum(
                "LEAD_IMPORT", "LEAD_UPDATE", "ACTION_IMPORT", "EXPORT", name="import_job_kind"
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "UPLOADED",
                "MAPPED",
                "PREVIEWED",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                name="import_job_status",
            ),
            server_default=sa.text("'UPLOADED'"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column(
            "source_columns",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "mapping",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("row_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("changeset_id", sa.UUID(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
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
            ["changeset_id"],
            ["changesets.id"],
            name=op.f("fk_import_jobs_changeset_id_changesets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["memberships.id"],
            name=op.f("fk_import_jobs_created_by_id_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_import_jobs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_jobs")),
    )
    op.create_index(
        "import_jobs_ws_created_idx", "import_jobs", ["workspace_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_import_jobs_workspace_id"), "import_jobs", ["workspace_id"], unique=False
    )
    op.create_table(
        "lead_labels",
        sa.Column("lead_id", sa.UUID(), nullable=False),
        sa.Column("label_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["labels.id"],
            name=op.f("fk_lead_labels_label_id_labels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name=op.f("fk_lead_labels_lead_id_leads"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_lead_labels_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("lead_id", "label_id", name=op.f("pk_lead_labels")),
    )
    op.create_index(
        op.f("ix_lead_labels_workspace_id"), "lead_labels", ["workspace_id"], unique=False
    )
    op.create_table(
        "tasks",
        sa.Column("lead_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_id", sa.UUID(), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
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
            ["assignee_id"],
            ["memberships.id"],
            name=op.f("fk_tasks_assignee_id_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_id"],
            ["memberships.id"],
            name=op.f("fk_tasks_completed_by_id_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["memberships.id"],
            name=op.f("fk_tasks_created_by_id_memberships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name=op.f("fk_tasks_lead_id_leads"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_tasks_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index(op.f("ix_tasks_workspace_id"), "tasks", ["workspace_id"], unique=False)
    op.create_index(
        "tasks_ws_assignee_due_idx",
        "tasks",
        ["workspace_id", "assignee_id", "due_at"],
        unique=False,
        postgresql_where=sa.text("completed_at IS NULL"),
    )
    op.create_index("tasks_ws_lead_idx", "tasks", ["workspace_id", "lead_id"], unique=False)


def downgrade() -> None:
    op.drop_index("tasks_ws_lead_idx", table_name="tasks")
    op.drop_index(
        "tasks_ws_assignee_due_idx",
        table_name="tasks",
        postgresql_where=sa.text("completed_at IS NULL"),
    )
    op.drop_index(op.f("ix_tasks_workspace_id"), table_name="tasks")
    op.drop_table("tasks")
    op.drop_index(op.f("ix_lead_labels_workspace_id"), table_name="lead_labels")
    op.drop_table("lead_labels")
    op.drop_index(op.f("ix_import_jobs_workspace_id"), table_name="import_jobs")
    op.drop_index("import_jobs_ws_created_idx", table_name="import_jobs")
    op.drop_table("import_jobs")
    op.drop_index(op.f("ix_labels_workspace_id"), table_name="labels")
    op.drop_table("labels")
    op.execute("DROP TYPE IF EXISTS import_job_status")
    op.execute("DROP TYPE IF EXISTS import_job_kind")
