"""Request and response shapes for search, saved filters and layouts (M6)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.filters.dsl import FilterNode
from app.models.enums import SavedFilterVisibility, StageKind

__all__ = [
    "FilterStats",
    "LayoutRead",
    "LayoutWrite",
    "LeadSearchRequest",
    "SavedFilterCreate",
    "SavedFilterRead",
    "SavedFilterReorder",
    "SavedFilterUpdate",
]


class LeadSearchRequest(BaseModel):
    """`POST /leads/search` — the ad-hoc filter DSL in a body.

    A POST rather than a GET with a query string because the DSL is a nested
    document: URL-encoding a filter tree would be unreadable in logs, would hit
    length limits on a real filter, and would put customer values in a URL,
    which is exactly where they must never go.
    """

    model_config = ConfigDict(extra="forbid")

    filter: FilterNode | None = None
    q: str | None = Field(default=None, max_length=200)
    sort: str = Field(default="-created_at", max_length=80)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    #: Quick filters — the current-state predicates that are columns on `leads`
    #: rather than workspace-defined fields, so the DSL cannot express them.
    stage_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    unassigned: bool = False
    rating: int | None = Field(default=None, ge=1, le=5)
    stage_kinds: list[StageKind] = Field(default_factory=list)
    #: Field keys the caller wants hydrated. `None` means every field they may
    #: view; an explicit list keeps a 50-column workspace's list response small.
    columns: list[str] | None = None


class SavedFilterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    definition: FilterNode
    visibility: SavedFilterVisibility = SavedFilterVisibility.PERSONAL
    template_id: uuid.UUID | None = None


class SavedFilterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    definition: FilterNode | None = None
    visibility: SavedFilterVisibility | None = None
    template_id: uuid.UUID | None = None


class SavedFilterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    definition: dict[str, Any]
    visibility: SavedFilterVisibility
    template_id: uuid.UUID | None
    owner_membership_id: uuid.UUID | None
    sort_order: int
    is_archived: bool
    created_at: dt.datetime


class SavedFilterReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Every id in the caller's visible set, in the order they should appear.
    filter_ids: list[uuid.UUID]


class FilterStats(BaseModel):
    """`GET /filters/{id}/stats` — how many leads the filter currently matches.

    Counted through the *caller's* visibility and grants, so two members can
    legitimately see different numbers for the same shared filter.
    """

    filter_id: uuid.UUID
    total: int
    by_stage: dict[str, int]


class LayoutWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(default_factory=list, max_length=100)
    column_widths: dict[str, int] = Field(default_factory=dict)
    sort_key: str | None = Field(default=None, max_length=80)
    sort_desc: bool = True


class LayoutRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filter_id: uuid.UUID | None
    columns: list[str]
    column_widths: dict[str, Any]
    sort_key: str | None
    sort_desc: bool
