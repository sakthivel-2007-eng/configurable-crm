"""SQLAlchemy models.

Importing this package registers every mapper, which is what lets SQLAlchemy
resolve the string forward-references between modules and what lets Alembic
autogenerate see the full metadata. `alembic/env.py` imports it for that reason.

Tables holding customer data inherit `TenantModel`, which declares
`workspace_id` and its cascade. The only tables that legitimately skip it are
`users`, `workspaces`, and `refresh_tokens`.
"""

from __future__ import annotations

from app.models.enums import (
    ActionDirection,
    ActionFieldType,
    AssignmentStrategy,
    AvailabilityStatus,
    ChangesetSource,
    ImportJobKind,
    ImportJobStatus,
    IndexedFieldStatus,
    LeadFieldType,
    PermissionGrant,
    SavedFilterVisibility,
    ScheduledReportFormat,
    StageKind,
    SystemActionKind,
    TemplateChannel,
)
from app.models.assignment import (
    AssignmentCursor,
    AssignmentRule,
    SalesGroup,
    SalesGroupMember,
    ScheduledReport,
)
from app.models.field import (
    ActionField,
    ActionFieldOption,
    CustomActionType,
    FieldOption,
    IndexedField,
    LeadField,
)
from app.models.lead import (
    Action,
    ActionAttachment,
    Changeset,
    Lead,
    MessageTemplate,
)
from app.models.mixins import Base, TenantModel, TimestampMixin
from app.models.permission import TemplateFieldGrant, TemplateLeadView
from app.models.pipeline import CallDisposition, LostReason, Stage
from app.models.user import RefreshToken, User
from app.models.view import SavedFilter, TableLayout
from app.models.work import ImportJob, Label, LeadLabel, Task
from app.models.workspace import (
    AvailabilityLog,
    Membership,
    PermissionTemplate,
    Workspace,
)

__all__ = [
    "Action",
    "ActionAttachment",
    "ActionDirection",
    "ActionField",
    "ActionFieldOption",
    "ActionFieldType",
    "AssignmentCursor",
    "AssignmentRule",
    "AssignmentStrategy",
    "AvailabilityLog",
    "AvailabilityStatus",
    "Base",
    "CallDisposition",
    "Changeset",
    "ChangesetSource",
    "CustomActionType",
    "FieldOption",
    "ImportJob",
    "ImportJobKind",
    "ImportJobStatus",
    "IndexedField",
    "IndexedFieldStatus",
    "Label",
    "Lead",
    "LeadField",
    "LeadFieldType",
    "LeadLabel",
    "LostReason",
    "Membership",
    "MessageTemplate",
    "PermissionGrant",
    "PermissionTemplate",
    "RefreshToken",
    "SalesGroup",
    "SalesGroupMember",
    "SavedFilter",
    "SavedFilterVisibility",
    "ScheduledReport",
    "ScheduledReportFormat",
    "Stage",
    "StageKind",
    "SystemActionKind",
    "TableLayout",
    "Task",
    "TemplateChannel",
    "TemplateFieldGrant",
    "TemplateLeadView",
    "TenantModel",
    "TimestampMixin",
    "User",
    "Workspace",
]
