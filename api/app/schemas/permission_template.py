"""Permission template schemas.

The summary is enough to populate a template picker and label a member row. The
detail, the field-grant matrix and the lead-view layout are M4's editor.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BulkGrantUpdate",
    "FieldGrantRow",
    "FieldGrantsUpdate",
    "LeadViewGroup",
    "LeadViewUpdate",
    "PermissionTemplateCreate",
    "PermissionTemplateDetail",
    "PermissionTemplateSummary",
    "PermissionTemplateUpdate",
]


class PermissionTemplateSummary(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    name: str
    is_system: bool
    is_readonly: bool


class PermissionTemplateDetail(BaseModel):
    """A template including its validated capability blob."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_system: bool
    is_readonly: bool
    capabilities: dict[str, Any] = Field(default_factory=dict)


class PermissionTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class PermissionTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    capabilities: dict[str, Any] | None = None


class FieldGrantRow(BaseModel):
    """One row of the matrix.

    An absent flag means denied. There is no explicit deny, so `false` and "not
    mentioned" are the same thing — which is what makes the matrix auditable.
    """

    model_config = ConfigDict(populate_by_name=True)

    field_id: uuid.UUID
    view: bool = False
    edit: bool = False
    # `import` is a Python keyword, so the field is named `import_` and aliased
    # back to the wire name the matrix uses.
    import_: bool = Field(default=False, alias="import")
    export: bool = False


class FieldGrantsUpdate(BaseModel):
    grants: list[FieldGrantRow]


class BulkGrantUpdate(BaseModel):
    """The column select-all control from §6.4."""

    grant: Literal["VIEW", "EDIT", "IMPORT", "EXPORT"]
    value: bool
    #: Omitted means "every field", which is what the header checkbox does.
    field_ids: list[uuid.UUID] | None = None


class LeadViewGroup(BaseModel):
    """One collapsible group in the lead-detail layout (§6.3)."""

    label: str = Field(default="Details", max_length=80)
    collapsed: bool = False
    field_ids: list[uuid.UUID] = Field(default_factory=list)


class LeadViewUpdate(BaseModel):
    layout: list[LeadViewGroup] = Field(default_factory=list)
