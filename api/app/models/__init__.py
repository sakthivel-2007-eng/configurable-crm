"""SQLAlchemy models.

Importing this package registers every mapper, which is what lets SQLAlchemy
resolve the string forward-references between modules and what lets Alembic
autogenerate see the full metadata. `alembic/env.py` imports it for that reason.

Tables holding customer data inherit `TenantModel`, which declares
`workspace_id` and its cascade. The only tables that legitimately skip it are
`users`, `workspaces`, and `refresh_tokens`.
"""

from __future__ import annotations

from app.models.enums import AvailabilityStatus
from app.models.mixins import Base, TenantModel, TimestampMixin
from app.models.user import RefreshToken, User
from app.models.workspace import (
    AvailabilityLog,
    Membership,
    PermissionTemplate,
    Workspace,
)

__all__ = [
    "AvailabilityLog",
    "AvailabilityStatus",
    "Base",
    "Membership",
    "PermissionTemplate",
    "RefreshToken",
    "TenantModel",
    "TimestampMixin",
    "User",
    "Workspace",
]
