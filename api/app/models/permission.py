"""Field-level permission grants and per-template lead layouts (M4).

`docs/01-data-model.md` §3.5. One row per (template, field, grant): presence
means granted, absence means denied. There is no explicit deny and no "inherit"
— the matrix is the whole truth, which is what makes it auditable.

`template_lead_views` implements "Set up your lead view" (§6.3): the per-template
lead-detail layout naming which fields appear, in what order, and which groups
start collapsed.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, PrimaryKeyConstraint, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enums import PermissionGrant
from app.models.mixins import TenantModel, TenantScoped, TimestampMixin


class TemplateFieldGrant(TenantScoped, TimestampMixin):
    """One cell of the field matrix (§6.4).

    A composite primary key rather than a surrogate id: the row *is* the fact
    "(template, field, grant)", and there is never more than one of it.

    Carries `workspace_id` like every tenant table, even though it is reachable
    through the template — architecture rule 1 is unconditional, and it lets a
    grant query filter on the tenant without a join.
    """

    __tablename__ = "template_field_grants"
    __table_args__ = (
        PrimaryKeyConstraint("template_id", "field_id", "grant", name="pk_template_field_grants"),
        Index("tfg_template_idx", "template_id", "grant"),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permission_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lead_fields.id", ondelete="CASCADE"),
        nullable=False,
    )
    grant: Mapped[PermissionGrant] = mapped_column(
        SAEnum(PermissionGrant, name="permission_grant", native_enum=True), nullable=False
    )


class TemplateLeadView(TenantModel):
    """ "Set up your lead view" — a per-template lead-detail layout (§6.3).

    Stored as an ordered JSONB list of groups rather than rows, because it is
    read whole, written whole, and never queried by its parts.
    """

    __tablename__ = "template_lead_views"
    __table_args__ = (UniqueConstraint("template_id", name="uq_template_lead_views_template_id"),)

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permission_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: `[{"label": str, "collapsed": bool, "field_ids": [uuid, ...]}, ...]`
    layout: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
