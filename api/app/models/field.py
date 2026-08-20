"""Field definitions — the configurable schema itself (M2).

`docs/01-data-model.md` §3.1 and §3.4. Four tables plus `indexed_fields`:

- `lead_fields` / `field_options`               — the lead schema
- `action_fields` / `action_field_options`      — per-custom-action-type forms
- `indexed_fields`                              — workspace-declared sort/filter
                                                  indexes over the JSONB blob

`custom_action_types` also lives here rather than in an M3 module, because
`action_fields.action_type_id` is a foreign key to it and the two tables have to
be created together. M3 owns its *behaviour* — code sequencing, score, archive
rules — but the table is part of this cluster.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.enums import ActionDirection, ActionFieldType, LeadFieldType
from app.models.mixins import TenantModel


class LeadField(TenantModel):
    """One admin-created field on the lead schema.

    `key` is derived from the label at creation and then **immutable**: it is
    the JSONB key every stored value is filed under, so renaming the label must
    not orphan data. `docs/01-data-model.md` §3.1 states this explicitly.

    Fields are hidden, never deleted (§1.1) — a deleted field would strand the
    values already written under its key.
    """

    __tablename__ = "lead_fields"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key", name="uq_lead_fields_workspace_id_key"),
        Index("ix_lead_fields_workspace_sort", "workspace_id", "sort_order"),
    )

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    field_type: Mapped[LeadFieldType] = mapped_column(
        # native_enum with a stable name so Alembic and Postgres agree.
        SAEnum(LeadFieldType, name="lead_field_type", native_enum=True),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)

    # Name / Phone / Email / Alternate Phone — renameable, never deletable.
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    field_group: Mapped[str | None] = mapped_column(String(80))

    # Properties, docs/03-configuration-model.md §1.4.
    show_in_import: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    show_in_quick_add: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    lock_after_create: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    can_use_variable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: Type-specific configuration: `parent_field_id` for DEPENDENT_DROPDOWN,
    #: `min`/`max` for NUMBER, `multiline` for TEXT. Shape is declared by the
    #: registry's `config_schema` so the settings UI renders it generically.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    options: Mapped[list[FieldOption]] = relationship(
        back_populates="field",
        cascade="all, delete-orphan",
        order_by="FieldOption.sort_order",
    )


class FieldOption(TenantModel):
    """One choice on a DROPDOWN, TAGS or DEPENDENT_DROPDOWN field.

    `parent_option_id` is what makes the dependent dropdown a tree: a child
    option names the parent it cascades from. A self-referencing FK rather than
    a separate table, because the cascade is arbitrary-depth in principle and
    two levels in practice.

    Carries `workspace_id` even though it is reachable through `field_id`.
    Architecture rule 1 is unconditional, and it lets the scoped session's
    loader criteria filter options directly.
    """

    __tablename__ = "field_options"
    __table_args__ = (
        UniqueConstraint("field_id", "code", name="uq_field_options_field_id_code"),
        Index("ix_field_options_field_sort", "field_id", "sort_order"),
        Index("ix_field_options_parent", "parent_option_id"),
    )

    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lead_fields.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_option_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("field_options.id", ondelete="CASCADE"),
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(70), nullable=False)
    color: Mapped[str | None] = mapped_column(String(9))
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    field: Mapped[LeadField] = relationship(back_populates="options")
    parent: Mapped[FieldOption | None] = relationship(
        remote_side="FieldOption.id", back_populates="children"
    )
    children: Mapped[list[FieldOption]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )


class CustomActionType(TenantModel):
    """An admin-defined timeline event with its own form.

    The table lands in M2 because `action_fields` points at it; M3 implements
    the rules (workspace-sequential code from 1001, archive, the score bounds
    enforced by the check constraint below).
    """

    __tablename__ = "custom_action_types"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_custom_action_types_workspace_id_code"),
        CheckConstraint("score BETWEEN -1000 AND 1000", name="ck_custom_action_types_score_range"),
    )

    code: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(64))
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    direction: Mapped[ActionDirection] = mapped_column(
        SAEnum(ActionDirection, name="action_direction", native_enum=True),
        nullable=False,
        default=ActionDirection.INFORMATION,
        server_default=text("'INFORMATION'::action_direction"),
    )
    description: Mapped[str | None] = mapped_column(Text)
    allow_predated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    fields: Mapped[list[ActionField]] = relationship(
        back_populates="action_type",
        cascade="all, delete-orphan",
        order_by="ActionField.sort_order",
    )


class ActionField(TenantModel):
    """A field on one custom action type's form.

    Same shape as `LeadField` but a different, smaller type registry (§4.3).
    Built as a second table rather than a discriminator column on `lead_fields`
    because the two have different owners and different lifecycles — an action
    field dies with its action type, a lead field never dies at all.
    """

    __tablename__ = "action_fields"
    __table_args__ = (
        UniqueConstraint("action_type_id", "key", name="uq_action_fields_action_type_id_key"),
        Index("ix_action_fields_type_sort", "action_type_id", "sort_order"),
    )

    action_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_action_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    field_type: Mapped[ActionFieldType] = mapped_column(
        SAEnum(ActionFieldType, name="action_field_type", native_enum=True),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)
    is_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    action_type: Mapped[CustomActionType] = relationship(back_populates="fields")
    options: Mapped[list[ActionFieldOption]] = relationship(
        back_populates="field",
        cascade="all, delete-orphan",
        order_by="ActionFieldOption.sort_order",
    )


class ActionFieldOption(TenantModel):
    """One choice on a DROPDOWN or TAGS action field."""

    __tablename__ = "action_field_options"
    __table_args__ = (
        UniqueConstraint(
            "action_field_id", "code", name="uq_action_field_options_action_field_id_code"
        ),
    )

    action_field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("action_fields.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(70), nullable=False)
    color: Mapped[str | None] = mapped_column(String(9))
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    field: Mapped[ActionField] = relationship(back_populates="options")


class IndexedField(TenantModel):
    """A workspace's declaration that one field should be sortable/filterable.

    `docs/01-data-model.md` §2.4. Because customer values live in JSONB, a sort
    on one needs an expression index. A workspace may declare up to 8; marking
    one enqueues the worker in `app.workers.indexing`, which is **the only
    place in the product that emits DDL at runtime** — `CREATE INDEX
    CONCURRENTLY` with a generated safe name, never `ALTER TABLE`.
    """

    __tablename__ = "indexed_fields"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "field_id", name="uq_indexed_fields_workspace_id_field_id"
        ),
    )

    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lead_fields.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Generated, never user-supplied — see `app.workers.indexing.index_name`.
    index_name: Mapped[str] = mapped_column(String(63), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default=text("'PENDING'")
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    field: Mapped[LeadField] = relationship()
