"""Member, licensing and availability schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import AvailabilityStatus
from app.schemas.auth import UserSummary

__all__ = [
    "AvailabilityLogEntry",
    "AvailabilityUpdate",
    "BulkUploadReport",
    "BulkUploadRow",
    "DeactivateRequest",
    "DeactivateResponse",
    "HierarchyNodeOut",
    "MemberDetail",
    "MemberInvite",
    "MemberUpdate",
    "SeatUsage",
]


class MemberInvite(BaseModel):
    model_config = ConfigDict(frozen=True)

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    template_id: uuid.UUID
    manager_id: uuid.UUID | None = None
    grant_license: bool = False


class MemberUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    template_id: uuid.UUID | None = None
    # Explicitly nullable: `{"manager_id": null}` clears the reporting line,
    # which is why this cannot be collapsed into a simple optional.
    manager_id: uuid.UUID | None = None
    clear_manager: bool = False


class MemberDetail(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    user: UserSummary
    template_id: uuid.UUID
    template_name: str
    manager_id: uuid.UUID | None
    is_active: bool
    has_license: bool
    availability: AvailabilityStatus
    created_at: datetime


class SeatUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    seats_used: int
    seat_limit: int


class AvailabilityUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: AvailabilityStatus
    note: str | None = Field(default=None, max_length=500)


class AvailabilityLogEntry(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    membership_id: uuid.UUID
    status: AvailabilityStatus
    note: str | None
    changed_by_id: uuid.UUID | None
    changed_at: datetime


class DeactivateRequest(BaseModel):
    """`reassign_to_membership_id` is required when the member holds open leads.

    It is optional on the schema and enforced in the service, because whether it
    is required depends on state the client cannot see. Omitting it when leads
    are held returns `409 reassignment_required` with the count.
    """

    model_config = ConfigDict(frozen=True)

    reassign_to_membership_id: uuid.UUID | None = None


class DeactivateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    member: MemberDetail
    leads_reassigned: int


class HierarchyNodeOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    member: MemberDetail
    reports: list[HierarchyNodeOut]


class BulkUploadRow(BaseModel):
    """One row's outcome in a bulk upload."""

    model_config = ConfigDict(frozen=True)

    row_number: int
    email: str | None
    full_name: str | None
    template_name: str | None
    manager_email: str | None
    status: str  # created | skipped | error
    message: str | None = None


class BulkUploadReport(BaseModel):
    """Dry-run or commit result.

    A dry run is the default: importing members is the kind of operation people
    want to see before it happens.
    """

    model_config = ConfigDict(frozen=True)

    dry_run: bool
    total_rows: int
    created: int
    skipped: int
    errored: int
    rows: list[BulkUploadRow]
