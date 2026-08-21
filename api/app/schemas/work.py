"""Request and response shapes for tasks, labels and imports (M7)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ImportJobKind, ImportJobStatus

__all__ = [
    "ImportJobRead",
    "ImportMappingWrite",
    "LabelCreate",
    "LabelRead",
    "LabelUpdate",
    "TaskCounts",
    "TaskCreate",
    "TaskRead",
    "TaskUpdate",
]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    due_at: dt.datetime
    lead_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    assignee_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    due_at: dt.datetime | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    assignee_id: uuid.UUID | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID | None
    title: str
    notes: str | None
    due_at: dt.datetime
    assignee_id: uuid.UUID | None
    completed_at: dt.datetime | None
    completed_by_id: uuid.UUID | None
    created_at: dt.datetime


class TaskCounts(BaseModel):
    """One number per bucket, for the badge on the nav item."""

    upcoming: int
    late: int
    done: int


class LabelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    color: str | None = Field(default=None, max_length=9)


class LabelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=60)
    color: str | None = Field(default=None, max_length=9)


class LabelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    color: str | None
    sort_order: int
    is_archived: bool


class ImportMappingWrite(BaseModel):
    """`{source column -> target}` plus how to run it.

    The mapping is column-keyed rather than field-keyed because the operator is
    looking at their spreadsheet, not at our schema — and because two columns
    cannot map to one field, while one field being left unmapped is normal.
    """

    model_config = ConfigDict(extra="forbid")

    mapping: dict[str, str] = Field(min_length=1)
    #: Strategy and its configuration. Shape depends on the job's kind; see
    #: `app.services.importing.DistributionStrategy`.
    options: dict[str, Any] = Field(default_factory=dict)


class ImportJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ImportJobKind
    status: ImportJobStatus
    filename: str
    source_columns: list[str]
    mapping: dict[str, Any]
    options: dict[str, Any]
    result: dict[str, Any]
    row_count: int
    changeset_id: uuid.UUID | None
    error: str | None
    created_at: dt.datetime
