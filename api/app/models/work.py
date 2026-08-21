"""Tasks, labels and import/export jobs (M7).

`docs/01-data-model.md` §6 names `labels`, `lead_labels` and `tasks`; the job
table is M7's own, because every import and export in the contract answers with
a `job_id` and something has to be behind it.

Two shapes worth explaining here rather than in a review comment:

**A task's bucket is derived, never stored.** `upcoming`, `late` and `done` are
questions about `due_at` and `completed_at` at the moment you ask, so storing a
bucket would mean a nightly job to move rows between them and a window every
morning where the answer was wrong.

**A job holds the *plan*, not the file.** The uploaded sheet lands in object
storage; what lives here is the mapping the operator chose, the preview it
produced, and the changeset it eventually wrote. That is what makes an import
auditable after the fact and undoable as a unit.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enums import ImportJobKind, ImportJobStatus
from app.models.mixins import TenantModel, TenantScoped

__all__ = ["ImportJob", "Label", "LeadLabel", "Task"]


class Task(TenantModel):
    """A piece of follow-up work, usually attached to a lead.

    `lead_id` is nullable so a standalone reminder is expressible, but the
    common case — and the one `/leads/{id}/tasks` serves — is a task about a
    lead.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        # The three list views the contract names, all of which read
        # "this member's incomplete work, soonest first".
        Index(
            "tasks_ws_assignee_due_idx",
            "workspace_id",
            "assignee_id",
            "due_at",
            postgresql_where=text("completed_at IS NULL"),
        ),
        Index("tasks_ws_lead_idx", "workspace_id", "lead_id"),
    )

    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    #: `timestamptz`, rendered in the *workspace's* timezone (rule 10). A task
    #: due "today" is due on the customer's today, not the server's.
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memberships.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memberships.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memberships.id", ondelete="SET NULL")
    )


class Label(TenantModel):
    """A free-form tag a workspace puts on leads.

    Distinct from a `TAGS` field on purpose: a field is part of the schema an
    admin designed, while a label is something a caller sticks on in the moment.
    Both exist in the source product and they are not the same tool.
    """

    __tablename__ = "labels"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_labels_workspace_id_name"),)

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    color: Mapped[str | None] = mapped_column(String(9))
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class LeadLabel(TenantScoped):
    """The join. Keyed on the pair, because the row *is* the fact.

    `TenantScoped` rather than `TenantModel` for the same reason
    `template_field_grants` is: a surrogate id here would add nothing, and the
    scoping listener targets `TenantScoped`, so the composite key is still
    tenant-filtered like everything else.
    """

    __tablename__ = "lead_labels"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class ImportJob(TenantModel):
    """One import or export run, from upload to committed changeset.

    Every import in the contract answers `{job_id}` rather than a result,
    because a 20,000-row sheet cannot be processed inside a request. The row
    carries the whole lifecycle so an operator can come back to a half-finished
    mapping, and so a finished run says what it did.
    """

    __tablename__ = "import_jobs"
    __table_args__ = (Index("import_jobs_ws_created_idx", "workspace_id", "created_at"),)

    kind: Mapped[ImportJobKind] = mapped_column(
        SAEnum(ImportJobKind, name="import_job_kind", native_enum=True), nullable=False
    )
    status: Mapped[ImportJobStatus] = mapped_column(
        SAEnum(ImportJobStatus, name="import_job_status", native_enum=True),
        nullable=False,
        default=ImportJobStatus.UPLOADED,
        server_default=text("'UPLOADED'"),
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Where the uploaded sheet lives. The file is not in the database.
    storage_key: Mapped[str | None] = mapped_column(String(512))
    #: Header row as uploaded, so the mapping UI can offer real column names.
    source_columns: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: `{source column -> target}`. Chosen by the operator, validated against
    #: the caller's Import grants before it is stored.
    mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    #: Strategy and its configuration — distribution, owner column, duplicate
    #: handling. Shape depends on `kind`.
    options: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    #: What the dry run found, and later what the commit did: counts, and the
    #: rows that could not be used.
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    row_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: The batch this run wrote, so a bad import is one undo.
    changeset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("changesets.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memberships.id", ondelete="SET NULL")
    )
