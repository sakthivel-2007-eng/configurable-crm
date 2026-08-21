"""Request and response shapes for sales groups, rules and distribution (M8)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AssignmentStrategy

__all__ = [
    "AssignmentPreviewRead",
    "AssignmentRuleCreate",
    "AssignmentRuleRead",
    "AssignmentRuleReorder",
    "AssignmentRuleUpdate",
    "DistributeRequest",
    "DistributionRead",
    "SalesGroupCreate",
    "SalesGroupMemberRead",
    "SalesGroupMemberWrite",
    "SalesGroupRead",
    "SalesGroupUpdate",
]


class SalesGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)


class SalesGroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    is_archived: bool | None = None


class SalesGroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    is_archived: bool


class SalesGroupMemberWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_id: uuid.UUID
    #: A member with weight 3 is dealt three times per round-robin cycle.
    weight: int = Field(default=1, ge=1, le=100)


class SalesGroupMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    membership_id: uuid.UUID
    weight: int


class AssignmentRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    strategy: AssignmentStrategy
    #: Strategy-dependent; validated server-side against the chosen strategy.
    config: dict[str, Any] = Field(default_factory=dict)
    #: The M6 filter DSL. `{}` is a catch-all.
    conditions: dict[str, Any] = Field(default_factory=dict)
    priority: int | None = Field(default=None, ge=0)
    skip_unavailable: bool = True
    is_active: bool = True


class AssignmentRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    strategy: AssignmentStrategy | None = None
    config: dict[str, Any] | None = None
    conditions: dict[str, Any] | None = None
    priority: int | None = Field(default=None, ge=0)
    skip_unavailable: bool | None = None
    is_active: bool | None = None


class AssignmentRuleReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Every rule, exactly once. A partial order would silently renumber the
    #: rules it omitted.
    order: list[uuid.UUID] = Field(min_length=1)


class AssignmentRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    priority: int
    strategy: AssignmentStrategy
    config: dict[str, Any]
    conditions: dict[str, Any]
    skip_unavailable: bool
    is_active: bool
    created_at: dt.datetime


class AssignmentPreviewRead(BaseModel):
    """A dry run: which rule fires, and who it picks."""

    model_config = ConfigDict(frozen=True)

    rule_id: uuid.UUID | None
    rule_name: str | None
    membership_id: uuid.UUID | None
    #: matched | no_rule_matched | no_candidates | rule_assigns_nobody
    reason: str


class DistributeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    strategy: AssignmentStrategy
    config: dict[str, Any] = Field(default_factory=dict)
    skip_unavailable: bool = True


class DistributionRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: The undo handle. One redistribution is one changeset.
    changeset_id: uuid.UUID | None
    assigned: int
    #: Already with that member, so nothing was written and nothing to undo.
    skipped: int
    total: int
