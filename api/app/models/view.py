"""Saved filters and table layouts (M6).

`docs/01-data-model.md` §6. Two tables that together make the lead list a place
people work rather than a place they re-type a query:

- `saved_filters` holds a filter DSL document plus who it is for.
- `table_layouts` holds one member's column choice *for one filter*, because
  the columns that make sense for "needs a call today" are not the ones that
  make sense for "closed this quarter".

Neither stores results, and neither stores a permission decision. A saved
filter is a question; who may see the answer is settled at run time by the
runner's own field grants.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enums import SavedFilterVisibility
from app.models.mixins import TenantModel

__all__ = ["SavedFilter", "TableLayout"]


class SavedFilter(TenantModel):
    """A named filter-DSL document, scoped to a person, a role, or everyone.

    `definition` is the same DSL the ad-hoc search endpoint accepts, stored
    verbatim. It is deliberately *not* compiled or denormalised on save: the
    workspace's fields and stages change under it, and a filter that was
    compiled at save time would keep answering a question the workspace has
    stopped asking. It is re-validated and re-compiled on every run.
    """

    __tablename__ = "saved_filters"
    __table_args__ = (
        Index(
            "saved_filters_ws_visibility_idx",
            "workspace_id",
            "visibility",
            postgresql_where=text("is_archived = false"),
        ),
        Index(
            "saved_filters_ws_owner_idx",
            "workspace_id",
            "owner_membership_id",
            postgresql_where=text("is_archived = false"),
        ),
        # A ROLE filter must name a template, and no other visibility may. Both
        # halves matter: a ROLE filter naming nothing is invisible to everyone,
        # and a PERSONAL one naming a template is a contradiction that some
        # later query would resolve arbitrarily.
        # Named without a prefix: the metadata naming convention expands `ck` to
        # `ck_%(table_name)s_%(constraint_name)s`, so spelling the full name
        # here would produce `ck_saved_filters_ck_saved_filters_...`.
        CheckConstraint(
            "(visibility = 'ROLE') = (template_id IS NOT NULL)",
            name="role_names_a_template",
        ),
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: The filter DSL document. Validated against `app.filters.dsl` on write so
    #: a malformed filter is rejected at save time rather than at every run.
    definition: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    visibility: Mapped[SavedFilterVisibility] = mapped_column(
        SAEnum(SavedFilterVisibility, name="saved_filter_visibility", native_enum=True),
        nullable=False,
        default=SavedFilterVisibility.PERSONAL,
        server_default=text("'PERSONAL'"),
    )
    #: Set only when `visibility` is ROLE. A check constraint keeps the two
    #: honest — a ROLE filter naming no template would be invisible to
    #: everyone, and a PERSONAL one naming a template is a contradiction.
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("permission_templates.id", ondelete="CASCADE")
    )
    #: The author. Nullable because deactivating the author must not delete a
    #: filter their whole team is using.
    owner_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memberships.id", ondelete="SET NULL")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: Archived, never deleted (architecture rule 13) — a filter id may be
    #: referenced by a table layout and, from M8, by a scheduled report.
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class TableLayout(TenantModel):
    """One member's column choice for one filter.

    Keyed on `(workspace_id, membership_id, filter_id)` with a null `filter_id`
    meaning the unfiltered list. Null cannot participate in a unique
    constraint, so the uniqueness is spelled as two partial indexes rather than
    one constraint — otherwise a member could accumulate any number of default
    layouts and the list would pick one arbitrarily.
    """

    __tablename__ = "table_layouts"
    __table_args__ = (
        Index(
            "table_layouts_member_filter_uq",
            "workspace_id",
            "membership_id",
            "filter_id",
            unique=True,
            postgresql_where=text("filter_id IS NOT NULL"),
        ),
        Index(
            "table_layouts_member_default_uq",
            "workspace_id",
            "membership_id",
            unique=True,
            postgresql_where=text("filter_id IS NULL"),
        ),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memberships.id", ondelete="CASCADE"),
        nullable=False,
    )
    filter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("saved_filters.id", ondelete="CASCADE")
    )
    #: Ordered column identifiers — built-in column names and `lead_fields.key`
    #: values in one list, because the user reorders them in one table. Stored
    #: as given; unknown or no-longer-visible keys are filtered out on read
    #: rather than pruned on write, so hiding a field temporarily does not
    #: silently discard everyone's layout.
    columns: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: Per-column widths in pixels, keyed by column id. Presentation only.
    column_widths: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    sort_key: Mapped[str | None] = mapped_column(String(80))
    sort_desc: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
