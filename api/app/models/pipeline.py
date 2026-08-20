"""Pipeline and taxonomy (M3).

`docs/01-data-model.md` §3.2 and §3.3. Three tables, all pure customer
vocabulary: what a workspace calls its pipeline positions, why it loses deals,
and how it categorises a call.

The product owns the *structure* — `stage_kind` says a pipeline has one
starting point, one won state, one lost state and any number of stages in
between. It does not own a single label. "New", "Contacted", "Won" and "Lost"
arrive as provisioned rows a workspace immediately renames, not as constants.

The cardinality rule is enforced by a partial unique index rather than
application code, because two concurrent requests can each pass a
`SELECT ... WHERE kind = 'WON'` check and both insert. The database is the only
place that check can be atomic.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enums import StageKind
from app.models.mixins import TenantModel

__all__ = ["CallDisposition", "LostReason", "Stage"]

#: docs/03-configuration-model.md §2.1 — observed cap, adopted deliberately.
MAX_LOST_REASONS = 25

#: §2.2 — the edit dialog's live counter stops at 28.
MAX_STAGE_LABEL = 28


class Stage(TenantModel):
    """One position in the pipeline.

    Cardinality (§2.1): exactly one live INITIAL, one WON and one LOST per
    workspace; any number of ACTIVE. Enforced by `stages_singleton_uq` below —
    a partial unique index over the three singleton kinds, ignoring archived
    rows so a workspace can archive and replace one.
    """

    __tablename__ = "stages"
    __table_args__ = (
        Index(
            "stages_singleton_uq",
            "workspace_id",
            "kind",
            unique=True,
            postgresql_where=text("kind IN ('INITIAL', 'WON', 'LOST') AND is_archived = false"),
        ),
        Index("ix_stages_workspace_sort", "workspace_id", "sort_order"),
    )

    kind: Mapped[StageKind] = mapped_column(
        SAEnum(StageKind, name="stage_kind", native_enum=True), nullable=False
    )
    label: Mapped[str] = mapped_column(String(MAX_STAGE_LABEL), nullable=False)
    color: Mapped[str] = mapped_column(
        String(9), nullable=False, default="#6b7280", server_default=text("'#6b7280'")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class LostReason(TenantModel):
    """Why a deal was lost. Required when a lead enters the LOST stage.

    Capped at 25 live per workspace (§2.1). The cap is enforced in the service
    with a clear 409 rather than by a constraint, because "you have reached the
    limit of 25" is a message and a constraint violation is a stack trace.
    """

    __tablename__ = "lost_reasons"
    __table_args__ = (Index("ix_lost_reasons_workspace_sort", "workspace_id", "sort_order"),)

    label: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class CallDisposition(TenantModel):
    """A call outcome (§3).

    Two tiers, both observed in the source system: **system** entries are
    archivable but not editable, **custom** entries are fully editable. Exactly
    one live entry carries `is_default`, enforced by a partial unique index for
    the same concurrency reason as stage cardinality.

    There is no telephony in v1. This list populates the disposition picker on
    the *manual* log-call form, with the default preselected when the entered
    duration exceeds the workspace's `connected_call_min_seconds`.
    """

    __tablename__ = "call_dispositions"
    __table_args__ = (
        Index(
            "call_dispositions_default_uq",
            "workspace_id",
            unique=True,
            postgresql_where=text("is_default = true AND is_archived = false"),
        ),
        Index("ix_call_dispositions_workspace_sort", "workspace_id", "sort_order"),
    )

    label: Mapped[str] = mapped_column(String(80), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: System entries ship with every workspace and cannot be edited — only
    #: archived. A workspace that does not use "Switched Off" hides it; it
    #: cannot rename it into something that breaks a shared report.
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
