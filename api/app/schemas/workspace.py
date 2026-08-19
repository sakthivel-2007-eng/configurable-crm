"""Workspace request and response schemas.

Note what `WorkspaceCreate` does *not* accept: no industry, no vertical, no
starter-pack selector. Provisioning creates structure only, and adding a
"template" parameter here is exactly how hardcoded taxonomy gets in.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["WorkspaceCreate", "WorkspaceDetail", "WorkspaceUpdate"]

_ISO_4217_LENGTH = 3


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=60, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")

    # Localisation, captured at signup. Defaults match the first client but are
    # per-workspace values, never assumptions — a US customer changes them here
    # and nothing downstream needs a code change.
    default_country_code: str = Field(default="91", max_length=5, pattern=r"^\d{1,4}$")
    timezone: str = Field(default="Asia/Kolkata", max_length=64)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    seat_limit: int = Field(default=3, ge=1, le=10_000)

    @field_validator("timezone")
    @classmethod
    def _require_known_timezone(cls, value: str) -> str:
        """Reject a timezone the scheduler could not later evaluate cron in."""
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    @field_validator("currency")
    @classmethod
    def _uppercase_currency(cls, value: str) -> str:
        return value.upper()


class WorkspaceUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    default_country_code: str | None = Field(default=None, max_length=5, pattern=r"^\d{1,4}$")
    timezone: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    connected_call_min_seconds: int | None = Field(default=None, ge=0, le=3600)
    session_timeout_minutes: int | None = Field(default=None, ge=1, le=10_080)
    seat_limit: int | None = Field(default=None, ge=1, le=10_000)
    leaderboard_metrics: dict[str, bool] | None = None
    features: dict[str, bool] | None = None


class WorkspaceDetail(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    default_country_code: str
    timezone: str
    currency: str
    connected_call_min_seconds: int
    session_timeout_minutes: int | None
    leaderboard_metrics: dict[str, Any]
    features: dict[str, Any]
    seat_limit: int
    seats_used: int
    is_active: bool
    # Populated from M2 onward, when `lead_fields` exists.
    identity_field_id: uuid.UUID | None
    primary_field_1_id: uuid.UUID | None
    primary_field_2_id: uuid.UUID | None
