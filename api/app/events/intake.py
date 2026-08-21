"""The inbound intake path (M10).

`docs/02-api-contract.md` is blunt about the rule that shapes this whole module:
**a rejected payload at 2am is a lost lead.** So unknown keys are accepted,
stored and reported in `warnings` — never refused. `ValueValidator` already
behaves that way; the job here is not to add strictness on top of it.

The same instinct runs through the rest:

**Every request is logged, rejections included.** "We posted it and nothing
arrived" is the support question this exists to answer, and it can only answer
it if the failures are in the table too.

**A `{{...}}` value is quarantined, not stored.** The legacy system persisted
unresolved templates as real option values and corrupted its own taxonomy.
Quarantine keeps the lead and surfaces the problem instead.

**Assignment rules run on intake**, because they run inside `create_lead` and
this calls it. One implementation, three callers — that was the point of putting
them there in M8 rather than in a router.

**`dedupe: update` never blanks existing data.** A partial payload from a form
that only collected a phone number must not wipe the name somebody typed in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.auth.api_keys import ApiKeyScope
from app.errors import api_error
from app.models import (
    ChangesetSource,
    DedupeMode,
    IntakeLogEntry,
    IntakeOutcome,
    Lead,
    LeadField,
    Membership,
    Stage,
    User,
)
from app.permissions import FieldProjectionService, FieldWriteFilter, load_grants
from app.services.leads import LeadService

__all__ = ["MAX_BATCH", "IntakeResult", "IntakeService"]

#: `POST /intake/leads/batch` — the contract's ceiling.
MAX_BATCH = 500
#: What we keep of a request body in the log. Enough to diagnose, bounded so a
#: runaway integration cannot fill the table.
MAX_LOGGED_BODY = 16_384


@dataclass(slots=True)
class IntakeResult:
    outcome: IntakeOutcome
    lead_id: uuid.UUID | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    status_code: int = 200


class IntakeService:
    """One workspace's intake, authenticated by an API key."""

    def __init__(self, scope: ApiKeyScope) -> None:
        self._scope = scope
        self._session = scope.session
        self._leads: LeadService | None = None

    async def _lead_service(self) -> LeadService:
        """A `LeadService` whose chokepoints are the *key's* template.

        Not an admin bypass. A key that cannot Edit a field is refused that
        field by name, exactly as a person would be — which is what makes the
        voice-agent contract's permission model work.
        """
        if self._leads is None:
            rows = await self._session.execute(
                self._session.select(LeadField).order_by(LeadField.sort_order)
            )
            fields = list(rows.scalars().all())
            grants = await load_grants(
                self._session,
                template_id=self._scope.template.id,
                is_admin=self._scope.is_admin,
                all_field_keys={f.id: f.key for f in fields},
            )
            self._leads = LeadService(
                self._session,
                workspace=self._scope.workspace,
                projection=FieldProjectionService(grants),
                write_filter=FieldWriteFilter(grants),
                # No membership, so no actor. The timeline records the change
                # without attributing it to a person who did not make it.
                actor_id=None,
                visible_membership_ids=frozenset(),
                sees_all=True,
            )
        return self._leads

    # --- lookups ------------------------------------------------------------

    async def _resolve_stage(self, raw: Any) -> uuid.UUID | None:
        """A stage id or its label. Unknown → 400, and logged."""
        if raw in (None, ""):
            return None
        text = str(raw)
        try:
            stage_id = uuid.UUID(text)
        except ValueError:
            stage_id = None

        rows = await self._session.execute(self._session.select(Stage))
        for stage in rows.scalars().all():
            if stage_id is not None and stage.id == stage_id:
                return uuid.UUID(str(stage.id))
            if stage.label.lower() == text.lower():
                return uuid.UUID(str(stage.id))
        raise api_error(400, "unknown_stage", f"No stage called {text!r}")

    async def _resolve_assignee(self, email: str | None) -> uuid.UUID | None:
        """An assignee by email. Unknown is a warning, not a rejection.

        Losing the routing is bad; losing the lead is worse. The assignment
        rules still get their turn, so an unrecognised email degrades to
        "assigned by the rules" rather than "discarded".
        """
        if not email:
            return None
        rows = await self._session.execute(
            self._session.select(Membership)
            .join(User, User.id == Membership.user_id)
            .where(User.email == email.lower())
        )
        membership = rows.scalars().first()
        return membership.id if membership else None

    # --- the write ----------------------------------------------------------

    async def ingest_lead(self, body: dict[str, Any]) -> IntakeResult:
        """One lead. Never raises for a *data* problem — it returns an outcome."""
        service = await self._lead_service()
        warnings: list[str] = []

        values = dict(body.get("values") or {})
        identity = body.get("identity")
        if identity not in (None, ""):
            identity_key = await service.identity_key()
            values.setdefault(identity_key, identity)

        try:
            dedupe = DedupeMode(str(body.get("dedupe") or "UPDATE").upper())
        except ValueError:
            raise api_error(
                400,
                "unknown_dedupe",
                "dedupe must be one of: update, skip, create_duplicate",
            ) from None

        stage_id = await self._resolve_stage(body.get("stage"))

        assignee_email = body.get("assignee_email")
        assignee_id = await self._resolve_assignee(assignee_email)
        if assignee_email and assignee_id is None:
            warnings.append(
                f"assignee_email {assignee_email!r} is not a member here; "
                "the assignment rules chose instead"
            )

        existing = await self._find_existing(service, values)

        if existing is not None and dedupe is DedupeMode.SKIP:
            return IntakeResult(
                outcome=IntakeOutcome.SKIPPED, lead_id=existing.id, warnings=warnings
            )

        if existing is not None and dedupe is DedupeMode.CREATE_DUPLICATE:
            # Say why rather than silently updating. The workspace's identity
            # field is unique by construction, so this mode cannot be honoured.
            raise api_error(
                409,
                "identity_exists",
                "That identity already exists and the identity field is unique, "
                "so a duplicate cannot be created",
            )

        if existing is not None:
            lead, writer = await service.update_lead(
                existing.id,
                # Merge: keys absent from the payload are not mentioned, so the
                # PATCH path leaves them alone. A form that only collected a
                # phone must not wipe the name somebody typed.
                values={k: v for k, v in values.items() if v not in (None, "")},
                stage_id=stage_id,
                assignee_id=assignee_id,
            )
            writer.changeset.source = ChangesetSource.INTAKE
            return IntakeResult(
                outcome=IntakeOutcome.UPDATED,
                lead_id=lead.id,
                warnings=warnings + self._value_warnings(service),
            )

        lead, writer = await service.create_lead(
            values=values, stage_id=stage_id, assignee_id=assignee_id
        )
        writer.changeset.source = ChangesetSource.INTAKE
        return IntakeResult(
            outcome=IntakeOutcome.CREATED,
            lead_id=lead.id,
            warnings=warnings + self._value_warnings(service),
            status_code=201,
        )

    def _value_warnings(self, service: LeadService) -> list[str]:
        """Unknown keys stored, and templates quarantined.

        Both are *accepted*, so this is the only place either becomes visible.
        Silence here would mean a field quietly not arriving with no trace.
        """
        found: list[str] = []
        report = service.last_validation
        if report is None:
            return found
        for key in report.unknown_keys:
            found.append(f"unknown field {key!r} was stored as-is")
        for entry in report.quarantined:
            found.append(
                f"{entry.field_key!r} looked like an unresolved template "
                f"({entry.raw!r}) and was quarantined rather than stored"
            )
        return found

    async def _find_existing(self, service: LeadService, values: dict[str, Any]) -> Lead | None:
        identity_key = await service.identity_key()
        raw = values.get(identity_key)
        if raw in (None, ""):
            return None
        normalised = await service.normalise_identity(str(raw))
        if normalised is None:
            return None
        rows = await self._session.execute(
            self._session.select(Lead).where(
                Lead.identity_value == normalised, Lead.deleted_at.is_(None)
            )
        )
        found: Lead | None = rows.scalars().first()
        return found

    # --- logging ------------------------------------------------------------

    async def log(
        self,
        *,
        endpoint: str,
        body: dict[str, Any],
        result: IntakeResult,
    ) -> None:
        """Record the request. Called for successes *and* rejections."""
        serialised = body
        try:
            import json

            if len(json.dumps(body)) > MAX_LOGGED_BODY:
                serialised = {"_truncated": True}
        except (TypeError, ValueError):  # pragma: no cover - body came from JSON
            serialised = {"_unserialisable": True}

        self._session.add(
            IntakeLogEntry(
                api_key_id=self._scope.api_key.id,
                endpoint=endpoint,
                outcome=result.outcome,
                status_code=result.status_code,
                request_body=serialised,
                warnings=result.warnings,
                lead_id=result.lead_id,
                error=result.error,
            )
        )
