"""Response schemas for the health endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ComponentStatus = Literal["ok", "error"]
OverallStatus = Literal["ok", "degraded"]


class ComponentHealth(BaseModel):
    """Result of probing one backing service."""

    model_config = ConfigDict(frozen=True)

    status: ComponentStatus
    latency_ms: float | None = Field(
        default=None, description="Round-trip time of the probe, milliseconds."
    )
    error: str | None = Field(
        default=None,
        description="Failure detail. Message text is only included outside production.",
    )


class HealthChecks(BaseModel):
    """The backing services M0 stands up."""

    model_config = ConfigDict(frozen=True)

    database: ComponentHealth
    redis: ComponentHealth
    object_storage: ComponentHealth


class HealthResponse(BaseModel):
    """`ok` only when every check passed; otherwise `degraded` with HTTP 503."""

    model_config = ConfigDict(frozen=True)

    status: OverallStatus
    service: str
    version: str
    environment: str
    checks: HealthChecks
