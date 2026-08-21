"""Request/response models for leads, actions and templates (M5)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import SystemActionKind, TemplateChannel

__all__ = [
    "ActionRead",
    "CallLogCreate",
    "ChangesetRead",
    "CustomActionLog",
    "LeadCreate",
    "LeadUpdate",
    "MessageTemplateCreate",
    "NoteCreate",
    "TemplateRenderRequest",
]


class LeadCreate(BaseModel):
    """`values` is keyed by `lead_fields.key`.

    There are no per-field parameters here and never will be: the schema is a
    customer's, so the payload has to be a map.
    """

    values: dict[str, Any] = Field(default_factory=dict)
    stage_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    rating: int | None = Field(default=None, ge=1, le=5)


class LeadUpdate(BaseModel):
    values: dict[str, Any] | None = None
    stage_id: uuid.UUID | None = None
    lost_reason_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    #: Keys the caller explicitly cleared. Distinguishes "set to null" from
    #: "not mentioned" — a PATCH must not wipe fields it never talked about,
    #: and JSON alone cannot express the difference.
    unset: list[str] | None = None


class ActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    changeset_id: uuid.UUID | None
    kind: SystemActionKind
    action_type_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    payload: dict[str, Any]
    body: str | None
    score_applied: int
    is_pinned: bool
    performed_at: dt.datetime
    created_at: dt.datetime


class ChangesetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    actor_id: uuid.UUID | None
    summary: str
    lead_count: int
    is_undone: bool
    undone_at: dt.datetime | None = None
    undone_by_id: uuid.UUID | None = None
    #: Set when this changeset *is* an undo, pointing at what it reversed. An
    #: edit report that cannot show which batch undid which is only half a
    #: report — and this is the only marker, since an undo carries no distinct
    #: source.
    undo_of_id: uuid.UUID | None = None
    created_at: dt.datetime


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class CallLogCreate(BaseModel):
    """Manual call logging. There is no telephony in v1."""

    direction: Literal["INCOMING", "OUTGOING", "MISSED"]
    disposition_id: uuid.UUID
    duration_seconds: int = Field(ge=0, le=86_400)
    notes: str | None = Field(default=None, max_length=10_000)


class CustomActionLog(BaseModel):
    action_type_id: uuid.UUID
    values: dict[str, Any] = Field(default_factory=dict)
    #: Rejected unless the action type sets `allow_predated`.
    performed_at: dt.datetime | None = None


class MessageTemplateCreate(BaseModel):
    channel: TemplateChannel
    name: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=20_000)
    subject: str | None = Field(default=None, max_length=500)
    #: Shared with the whole workspace rather than personal.
    shared: bool = False
    #: Scope to one permission template's holders.
    role_template_id: uuid.UUID | None = None


class TemplateRenderRequest(BaseModel):
    lead_id: uuid.UUID


class LeadBulkUpdate(BaseModel):
    """`POST /leads/bulk` — one change applied to many leads.

    Explicit ids rather than a filter: this is what a table with checkboxes
    sends, and it means the operator's undo preview lists exactly the leads
    they picked. Redistributing a *filtered* set is `POST /leads/distribute`,
    which lands in M8.
    """

    model_config = ConfigDict(extra="forbid")

    lead_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    values: dict[str, Any] | None = None
    stage_id: uuid.UUID | None = None
    lost_reason_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    unset: list[str] | None = None


class UndoRequest(BaseModel):
    """`POST /changesets/{id}/undo`.

    `skip_conflicts` defaults to false, so the first call on a batch with
    conflicts refuses and shows them. Undoing over someone else's later edit
    has to be a decision somebody made, not the default.
    """

    model_config = ConfigDict(extra="forbid")

    skip_conflicts: bool = False


class LeadMerge(BaseModel):
    """`POST /leads/merge` — fold duplicates into one record."""

    model_config = ConfigDict(extra="forbid")

    primary_id: uuid.UUID
    #: Bounded: merging dozens at once is a sign the duplicate detection needs
    #: fixing rather than a bigger batch button.
    merge_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
