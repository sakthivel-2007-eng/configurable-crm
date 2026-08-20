"""Request/response models for the field definition engine (M2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ActionFieldType, LeadFieldType

__all__ = [
    "ActionFieldCreate",
    "ActionFieldOptionRead",
    "ActionFieldRead",
    "ActionFieldUpdate",
    "FieldOptionBulkCreate",
    "FieldOptionCreate",
    "FieldOptionRead",
    "FieldOptionReorder",
    "FieldOptionUpdate",
    "FieldTypeRead",
    "IdentityFieldUpdate",
    "IndexedFieldCreate",
    "IndexedFieldRead",
    "LeadFieldCreate",
    "LeadFieldRead",
    "LeadFieldUpdate",
    "PrimaryFieldsUpdate",
    "RecurringOccurrence",
]


class FieldOptionRead(BaseModel):
    """One choice, including its place in a dependent dropdown's tree."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    label: str
    color: str | None
    sort_order: int
    is_archived: bool
    parent_option_id: uuid.UUID | None


class FieldOptionCreate(BaseModel):
    label: str = Field(min_length=1, max_length=70)
    color: str | None = Field(default=None, max_length=9)
    parent_option_id: uuid.UUID | None = None


class FieldOptionUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=70)
    color: str | None = Field(default=None, max_length=9)
    sort_order: int | None = None


class FieldOptionBulkCreate(BaseModel):
    """ "Add multiple" — the drawer's bulk-paste box, one option per line."""

    labels: list[str] = Field(min_length=1, max_length=500)


class FieldOptionReorder(BaseModel):
    ordered_ids: list[uuid.UUID]


class LeadFieldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    label: str
    field_type: LeadFieldType
    description: str | None
    is_builtin: bool
    is_hidden: bool
    is_required: bool
    sort_order: int
    field_group: str | None
    show_in_import: bool
    show_in_quick_add: bool
    lock_after_create: bool
    can_use_variable: bool
    config: dict[str, Any]
    options: list[FieldOptionRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LeadFieldCreate(BaseModel):
    """`key` is absent by design — it is derived from the label and immutable."""

    label: str = Field(min_length=1, max_length=40)
    field_type: LeadFieldType
    description: str | None = None
    is_required: bool = False
    field_group: str | None = Field(default=None, max_length=80)
    config: dict[str, Any] = Field(default_factory=dict)
    show_in_import: bool = True
    show_in_quick_add: bool = False
    lock_after_create: bool = False
    can_use_variable: bool = False


class LeadFieldUpdate(BaseModel):
    """`field_type` is absent by design.

    Changing a field's type would invalidate every value already stored under
    its key. It is a create-time decision.
    """

    label: str | None = Field(default=None, min_length=1, max_length=40)
    description: str | None = None
    is_required: bool | None = None
    field_group: str | None = Field(default=None, max_length=80)
    config: dict[str, Any] | None = None
    show_in_import: bool | None = None
    show_in_quick_add: bool | None = None
    lock_after_create: bool | None = None
    can_use_variable: bool | None = None
    sort_order: int | None = None


class FieldTypeRead(BaseModel):
    """One registry entry, as the frontend consumes it.

    This is what stops the frontend hardcoding a type list: it asks for the
    registry and renders whatever it is told, including the widget contract.
    """

    key: str
    label: str
    description: str
    storage: str
    uses_options: bool
    operators: list[str]
    renderer: dict[str, Any]
    config_schema: dict[str, Any]


class IdentityFieldUpdate(BaseModel):
    field_id: uuid.UUID


class PrimaryFieldsUpdate(BaseModel):
    h1_field_id: uuid.UUID
    h2_field_id: uuid.UUID | None = None


class IndexedFieldCreate(BaseModel):
    field_id: uuid.UUID


class IndexedFieldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    field_id: uuid.UUID
    index_name: str
    status: str
    last_error: str | None
    created_at: datetime


class RecurringOccurrence(BaseModel):
    """One upcoming occurrence of a RECURRING_DATE field on one lead."""

    lead_id: uuid.UUID
    field_key: str
    field_label: str
    occurs_on: str
    start: str
    frequency: str
    interval: int


# --- action fields (the same builder, the other registry) --------------------


class ActionFieldOptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    label: str
    color: str | None
    sort_order: int


class ActionFieldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_type_id: uuid.UUID
    key: str
    label: str
    field_type: ActionFieldType
    description: str | None
    is_required: bool
    is_hidden: bool
    sort_order: int
    config: dict[str, Any]
    options: list[ActionFieldOptionRead] = Field(default_factory=list)


class ActionFieldCreate(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    field_type: ActionFieldType
    description: str | None = None
    is_required: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    options: list[FieldOptionCreate] = Field(default_factory=list)


class ActionFieldUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=40)
    description: str | None = None
    is_required: bool | None = None
    is_hidden: bool | None = None
    sort_order: int | None = None
    config: dict[str, Any] | None = None
