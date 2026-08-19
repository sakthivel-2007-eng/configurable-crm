"""Permission template schemas.

M1 needs only the summary — enough to populate a template picker and label a
member row. The full representation, including the field-grant matrix, is M4's.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

__all__ = ["PermissionTemplateSummary"]


class PermissionTemplateSummary(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    name: str
    is_system: bool
    is_readonly: bool
