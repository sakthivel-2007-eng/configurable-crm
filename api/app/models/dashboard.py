"""User-composed and role-bound dashboards (M9).

`docs/01-data-model.md` §5.5. `scheduled_reports` shares that section but landed
in M8, because a schedule needs a scheduler and a dashboard does not.

**`layout` is one JSONB array, not a table of widgets.** A dashboard is edited
as a whole — drag one widget and three others move — so a per-widget table would
mean a delete-and-reinsert on every drag, and a half-applied layout if anything
went wrong mid-write. The array is the unit of change because the *screen* is.

**Ownership and binding are two different nulls.** `owner_id` null means shared
with the workspace; `template_id` non-null means every member on that permission
template gets it. A dashboard can be both — shared *and* role-bound — which is
what "give the callers this dashboard" actually means.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mixins import TenantModel

__all__ = ["Dashboard"]


class Dashboard(TenantModel):
    """One composed screen of widgets (§5.5)."""

    __tablename__ = "dashboards"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Null means shared with the whole workspace.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE")
    )
    #: Non-null binds it to a permission template — everyone on that template
    #: gets it, without anyone having to assign it person by person.
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("permission_templates.id", ondelete="CASCADE")
    )
    #: `[{widget, x, y, w, h, config}]`. Validated against the widget catalogue
    #: on write, so a layout cannot name a widget that does not exist.
    layout: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(), nullable=False, server_default=text("'[]'::jsonb")
    )
    #: The owner's landing dashboard. At most one per owner, enforced in the
    #: service rather than by a partial unique index, because "at most one where
    #: owner_id is null" and "at most one per owner" are different rules and a
    #: single index cannot say both.
    is_default: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default=text("false")
    )
    #: Archived, not deleted — rule 13. A shared dashboard somebody built their
    #: morning around should not vanish from under them because its author
    #: tidied up, and the row costs nothing.
    is_archived: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", "owner_id", name="dashboards_name_uq"),
        Index("ix_dashboards_owner", "workspace_id", "owner_id"),
        Index("ix_dashboards_template", "workspace_id", "template_id"),
    )
