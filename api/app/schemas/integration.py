"""Request and response shapes for intake and outbound (M10)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IntakeOutcome, OutboxStatus

__all__ = [
    "ApiKeyCreate",
    "ApiKeyCreated",
    "ApiKeyRead",
    "IntakeBatchRequest",
    "IntakeBatchResult",
    "IntakeLeadRequest",
    "IntakeLogRead",
    "IntakeResponse",
    "OutboxEventRead",
    "WebhookCreate",
    "WebhookCreated",
    "WebhookRead",
    "WebhookTestResult",
    "WebhookUpdate",
]


class IntakeLeadRequest(BaseModel):
    """`POST /intake/leads`.

    Deliberately **not** `extra="forbid"`. The contract is explicit: a rejected
    payload at 2am is a lost lead, so an unrecognised top-level key is tolerated
    the same way an unrecognised field key is.
    """

    model_config = ConfigDict(extra="allow")

    identity: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    #: A stage id or its label.
    stage: str | None = None
    assignee_email: str | None = None
    #: update | skip | create_duplicate
    dedupe: str = "update"


class IntakeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: IntakeOutcome
    lead_id: uuid.UUID | None
    #: Unknown keys stored, quarantined templates, an unrecognised assignee.
    #: Everything the request got *away* with rather than everything it did.
    warnings: list[str] = Field(default_factory=list)


class IntakeBatchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    leads: list[IntakeLeadRequest] = Field(min_length=1, max_length=500)


class IntakeBatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    results: list[IntakeResponse]
    created: int
    updated: int
    skipped: int
    rejected: int


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    #: Whose field permissions this key's reads and writes are subject to.
    permission_template_id: uuid.UUID


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    #: The only plaintext kept, so the list can identify a key without being
    #: able to reproduce it.
    prefix: str
    permission_template_id: uuid.UUID
    last_used_at: dt.datetime | None
    revoked_at: dt.datetime | None
    created_at: dt.datetime


class ApiKeyCreated(ApiKeyRead):
    """The one and only response that carries the plaintext."""

    key: str


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2_000)
    #: Empty means every event.
    events: list[str] = Field(default_factory=list)
    permission_template_id: uuid.UUID


class WebhookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=1, max_length=2_000)
    events: list[str] | None = None
    permission_template_id: uuid.UUID | None = None
    is_active: bool | None = None


class WebhookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    events: list[str]
    permission_template_id: uuid.UUID
    is_active: bool
    created_at: dt.datetime


class WebhookCreated(WebhookRead):
    """Carries the signing secret. Shown once, like a key."""

    secret: str


class WebhookTestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    delivered: bool
    status_code: int | None
    error: str | None
    #: Echoed so the operator can paste it into their verifier and confirm both
    #: sides compute the same digest.
    signature: str


class OutboxEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event: str
    #: Stable across retries — a consumer's dedupe key.
    event_id: uuid.UUID
    endpoint_id: uuid.UUID
    status: OutboxStatus
    attempts: int
    occurred_at: dt.datetime
    next_attempt_at: dt.datetime
    last_error: str | None
    last_status_code: int | None
    delivered_at: dt.datetime | None


class IntakeLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    api_key_id: uuid.UUID | None
    endpoint: str
    outcome: IntakeOutcome
    status_code: int
    warnings: list[str]
    lead_id: uuid.UUID | None
    error: str | None
    created_at: dt.datetime
    request_body: dict[str, Any]
