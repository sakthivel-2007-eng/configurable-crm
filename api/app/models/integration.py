"""API keys, webhooks, the outbox and the intake log (M10).

`docs/01-data-model.md` §6 names all four; the shapes come from
`02-api-contract.md` §Intake and §Outbound.

Four things here are load-bearing and easy to get wrong:

**A key is stored hashed, shown once.** `prefix` is the only plaintext kept, so
the settings screen can say *which* key without being able to reconstruct it. A
leaked key is a lead firehose in both directions, so it gets the same treatment
as a password.

**A key carries a permission template.** That is the whole machine-auth model —
`load_grants` takes a `template_id`, so projection and write filtering work on
the intake and webhook paths unchanged. See `docs/06-voice-integration-contract.md`
§3, which depends on exactly this.

**An outbox row is written in the same transaction as the change it describes.**
That is what makes the bus reliable: there is no window where the lead moved and
the event did not. Rule 8 forbids the alternative.

**`event_id` is stable across retries.** Consumers dedupe on it; regenerating it
per attempt would turn one retried delivery into eight distinct events at the
far end.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.enums import IntakeOutcome, OutboxStatus
from app.models.mixins import TenantModel

__all__ = ["ApiKey", "IntakeLogEntry", "OutboxEvent", "WebhookEndpoint"]

outbox_status_enum = SAEnum(
    OutboxStatus,
    name="outbox_status",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

intake_outcome_enum = SAEnum(
    IntakeOutcome,
    name="intake_outcome",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class ApiKey(TenantModel):
    """A machine caller (§Intake, §Outbound).

    Not a member: it has no availability, holds no leads, and never appears in a
    leaderboard. What it has is a permission template, which is what makes every
    read it drives projectable and every write it drives filterable.
    """

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The first characters of the key, in plaintext, so the settings list can
    #: identify one without being able to reproduce it.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Argon2, exactly like a password. The plaintext is shown once at creation
    #: and never stored.
    hashed_key: Mapped[str] = mapped_column(Text(), nullable=False)
    #: Whose field permissions this key's reads and writes are subject to.
    permission_template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permission_templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memberships.id", ondelete="SET NULL")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Revoked rather than deleted: the intake log references it, and "which key
    #: posted this bad data" is the first question after an incident.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="api_keys_name_uq"),
        Index("ix_api_keys_prefix", "prefix"),
    )


class WebhookEndpoint(TenantModel):
    """Where outbound events go (§Outbound)."""

    __tablename__ = "webhook_endpoints"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(Text(), nullable=False)
    #: Signing secret. Shown once, like a key — the consumer needs it to verify
    #: `X-CRM-Signature`, and after that nobody here does.
    secret: Mapped[str] = mapped_column(Text(), nullable=False)
    #: Event names this endpoint wants. Empty means every event.
    events: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    #: A webhook is a read path like any other, so its payload is projected
    #: through this template's grants. Without it the bus would be a hole in the
    #: field matrix.
    permission_template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permission_templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=True, server_default=text("true")
    )

    __table_args__ = (UniqueConstraint("workspace_id", "name", name="webhook_endpoints_name_uq"),)


class OutboxEvent(TenantModel):
    """One pending delivery, written with the change it describes (§Outbound)."""

    __tablename__ = "outbox_events"

    #: `lead.stage_changed`, `action.created`, …
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    #: Sent as `X-CRM-Event-Id` and stable across retries, so a consumer that
    #: dedupes sees one event however many times we deliver it.
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=func.gen_random_uuid()
    )
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The `data` block, unprojected. Projection happens at delivery, against the
    #: endpoint's template, so changing a template does not require rewriting
    #: queued rows — and a row queued before a permission was revoked does not
    #: escape it.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[OutboxStatus] = mapped_column(
        outbox_status_enum,
        nullable=False,
        default=OutboxStatus.PENDING,
        server_default=text("'PENDING'"),
    )
    attempts: Mapped[int] = mapped_column(
        SmallInteger(), nullable=False, default=0, server_default=text("0")
    )
    #: When this row becomes eligible again. `2^attempts` minutes, capped at 60.
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Set while a worker holds the row, so a crashed delivery can be recognised
    #: and reclaimed rather than sitting in DELIVERING forever.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text())
    #: The HTTP status the endpoint last answered, for the operator's view.
    last_status_code: Mapped[int | None] = mapped_column(Integer())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # The delivery worker's only query: eligible rows, oldest first.
        Index(
            "ix_outbox_due",
            "status",
            "next_attempt_at",
            postgresql_where=text("status IN ('PENDING', 'FAILED')"),
        ),
        Index("ix_outbox_workspace_created", "workspace_id", "occurred_at"),
    )


class IntakeLogEntry(TenantModel):
    """Every intake request, rejections included (§Intake).

    Rejections *especially*. "We posted it and nothing arrived" is the support
    question this table exists to answer, and it can only answer it if the
    failures are in it too.
    """

    __tablename__ = "intake_log"

    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    #: `leads`, `actions`, `leads/batch`.
    endpoint: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome: Mapped[IntakeOutcome] = mapped_column(intake_outcome_enum, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer(), nullable=False)
    #: What arrived, as it arrived. Bounded on write.
    request_body: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=text("'{}'::jsonb")
    )
    #: Unknown keys stored, and any `{{...}}` values quarantined. Both are
    #: accepted rather than refused, so this is the only place they surface.
    warnings: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text())

    __table_args__ = (
        Index("ix_intake_log_recent", "workspace_id", "created_at"),
        Index("ix_intake_log_outcome", "workspace_id", "outcome", "created_at"),
    )
