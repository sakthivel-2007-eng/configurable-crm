"""Sales groups, assignment rules, and scheduled reports (M8).

`docs/01-data-model.md` §5.2, §5.3 and §5.5.

Three shapes worth explaining here rather than in a review comment:

**A rule's conditions are the M6 filter DSL, unchanged.** Not a second
condition language — the same `conditions` JSONB the saved views store, and the
same `FilterCompiler` evaluates it. The engine asks "does this one lead match?"
by compiling the rule to SQL and scoping it to that lead's id, which is why
there is no in-Python evaluator anywhere in M8.

**The cursor is a row, and it is locked.** `assignment_cursors` exists so
round-robin survives a restart, but its real job is to be a lock target. Two
leads arriving in the same millisecond must not both read cursor position 4 and
both go to the fifth rep; the engine takes `SELECT ... FOR UPDATE` on this row
and serialises the pair. The table would be unnecessary if that were not true —
the position could be derived from the last assignment.

**`config` is deliberately loose JSONB.** Each strategy reads different keys —
a group id, a field key plus a value map, a fixed membership id — and giving
each its own nullable column would mean five columns of which four are always
null. The service validates the shape per strategy on write.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enums import AssignmentStrategy, ScheduledReportFormat
from app.models.mixins import TenantModel, TenantScoped

__all__ = [
    "AssignmentCursor",
    "AssignmentRule",
    "SalesGroup",
    "SalesGroupMember",
    "ScheduledReport",
]

assignment_strategy_enum = SAEnum(
    AssignmentStrategy,
    name="assignment_strategy",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

scheduled_report_format_enum = SAEnum(
    ScheduledReportFormat,
    name="scheduled_report_format",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class SalesGroup(TenantModel):
    """A named set of members (§5.2).

    Two consumers, which is why it is not just an assignment detail: a
    distribution target, and a report segment in M9.
    """

    __tablename__ = "sales_groups"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text())
    is_archived: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (UniqueConstraint("workspace_id", "name", name="sales_groups_name_uq"),)


class SalesGroupMember(TenantScoped):
    """Membership in a group, with a dealing weight (§5.2).

    Composite-keyed, so it inherits `TenantScoped` rather than `TenantModel` —
    the row *is* the fact. See the note in `models/mixins.py` about why that
    split matters for isolation rather than tidiness.
    """

    __tablename__ = "sales_group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sales_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memberships.id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: A member with weight 3 is dealt three times per round-robin cycle.
    weight: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=1, server_default=text("1")
    )

    __table_args__ = (
        CheckConstraint("weight >= 1 AND weight <= 100", name="sales_group_weight_range"),
    )


class AssignmentRule(TenantModel):
    """One priority-ordered rule (§5.3)."""

    __tablename__ = "assignment_rules"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Lowest number wins. Not unique — reordering a list with a unique index
    #: means a dance of temporary values for no benefit.
    priority: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0, server_default=text("0")
    )
    #: The M6 filter DSL. `{}` means "matches everything" — a catch-all.
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, server_default="{}")
    strategy: Mapped[AssignmentStrategy] = mapped_column(assignment_strategy_enum, nullable=False)
    #: Strategy-dependent. Validated per strategy in the service, not here.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, server_default="{}")
    #: Skip members whose availability is not WORKING.
    skip_unavailable: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default=text("true")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default=text("true")
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="assignment_rules_name_uq"),
        Index("ix_assignment_rules_order", "workspace_id", "priority", "id"),
    )


class AssignmentCursor(TenantScoped):
    """Round-robin position for one rule (§5.3).

    Keyed on `rule_id` because there is exactly one cursor per rule, and
    because that makes it a single unambiguous row to take `FOR UPDATE` on.
    """

    __tablename__ = "assignment_cursors"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assignment_rules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memberships.id", ondelete="SET NULL")
    )
    #: How far into the *weighted* cycle we are. Plain round-robin ignores it.
    position: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ScheduledReport(TenantModel):
    """A saved report plus a cadence and recipients (§5.5).

    `cron` is evaluated in the **workspace** timezone, not the server's, and the
    report renders as `created_by` so the field matrix governs what reaches the
    recipient's inbox. Both are easy to get wrong and neither is visible in a
    single-timezone, single-member test.
    """

    __tablename__ = "scheduled_reports"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: leaderboard | activity | funnel | leads. Text rather than an enum: M9
    #: owns the report catalogue and will add to it.
    report_type: Mapped[str] = mapped_column(String(40), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, server_default="{}")
    #: Five-field cron. Validated on write.
    cron: Mapped[str] = mapped_column(String(120), nullable=False)
    recipients: Mapped[list[str]] = mapped_column(ARRAY(Text()), nullable=False)
    format: Mapped[ScheduledReportFormat] = mapped_column(
        scheduled_report_format_enum,
        nullable=False,
        default=ScheduledReportFormat.CSV,
        server_default=text("'CSV'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default=text("true")
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set when a run fails, cleared when one succeeds, so the settings list can
    #: show a broken schedule instead of it failing silently every morning.
    last_error: Mapped[str | None] = mapped_column(Text())
    #: Whose field permissions the render uses. Null once that member is gone,
    #: which deactivates the schedule rather than silently escalating it.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memberships.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="scheduled_reports_name_uq"),
        Index("ix_scheduled_reports_active", "workspace_id", "is_active"),
    )
