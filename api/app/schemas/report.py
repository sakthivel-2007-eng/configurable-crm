"""Request and response shapes for reports and dashboards (M9)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BucketRead",
    "DashboardCreate",
    "DashboardRead",
    "DashboardUpdate",
    "FollowUpCounts",
    "LeaderboardRowRead",
    "WidgetSpec",
]


class BucketRead(BaseModel):
    """One bar. `key` is what a drill-through filters on; `label` is what a
    person reads. They differ for a stage (uuid vs name) and coincide for a
    field value, and conflating them would make either the chart unreadable or
    the drill-through unfilterable."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    count: int


class FollowUpCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    late: int
    upcoming: int
    never_contacted: int


class LeaderboardRowRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    membership_id: uuid.UUID
    name: str
    #: Only the metrics this workspace turned on, plus leads and calls.
    metrics: dict[str, int]


class WidgetSpec(BaseModel):
    """One entry in the catalogue, with the schema its config form is built
    from — so the editor can render a widget it has never heard of."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    description: str
    #: stats | bar | funnel | table
    shape: str
    config: dict[str, Any]
    default_size: dict[str, int]


class DashboardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    layout: list[dict[str, Any]] = Field(default_factory=list)
    #: Shared with the whole workspace rather than personal.
    shared: bool = False
    #: Bind to a permission template — everyone on it gets this dashboard.
    template_id: uuid.UUID | None = None


class DashboardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    layout: list[dict[str, Any]] | None = None
    template_id: uuid.UUID | None = None


class DashboardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    owner_id: uuid.UUID | None
    template_id: uuid.UUID | None
    layout: list[dict[str, Any]]
    is_default: bool
    created_at: dt.datetime
