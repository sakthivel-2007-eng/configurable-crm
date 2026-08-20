"""Lead persistence (M5).

Every read here goes through `FieldProjectionService` and every write through
`FieldWriteFilter` — no exceptions, no shortcut for "internal" calls, because an
internal caller is exactly how the first leak happens.

The mutation shape is the same everywhere:

    open a changeset -> apply the change -> append the actions -> commit once

All in one transaction, so the timeline can never diverge from the data it
describes.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.errors import api_error, conflict, not_found
from app.fields.values import FieldValidationError, ValueValidator
from app.models.enums import ChangesetSource, StageKind
from app.models.field import FieldOption, LeadField
from app.models.lead import Action, Lead
from app.models.pipeline import LostReason, Stage
from app.models.workspace import Membership, Workspace
from app.permissions.projection import FieldProjectionService, FieldWriteFilter
from app.services.actions import ActionWriter, FieldDelta
from app.tenancy.session import ScopedSession

__all__ = ["LeadService"]


class LeadService:
    """Create, read and update leads.

    Constructed per request with the caller's projection and write filter
    already bound, so no method can accidentally run unfiltered.
    """

    def __init__(
        self,
        session: ScopedSession,
        *,
        workspace: Workspace,
        projection: FieldProjectionService,
        write_filter: FieldWriteFilter,
        actor_id: uuid.UUID | None,
        visible_membership_ids: frozenset[uuid.UUID],
        sees_all: bool,
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._projection = projection
        self._write_filter = write_filter
        self._actor_id = actor_id
        self._visible = visible_membership_ids
        self._sees_all = sees_all
        self._validator: ValueValidator | None = None
        self._fields: list[LeadField] = []

    # --- setup -------------------------------------------------------------

    async def _load_schema(self) -> ValueValidator:
        """Build the validator once per request from the workspace's schema."""
        if self._validator is None:
            rows = await self._session.execute(
                self._session.select(LeadField).order_by(LeadField.sort_order)
            )
            self._fields = list(rows.scalars().all())

            option_rows = await self._session.execute(self._session.select(FieldOption))
            grouped: dict[uuid.UUID, list[FieldOption]] = {}
            for option in option_rows.scalars().all():
                grouped.setdefault(option.field_id, []).append(option)

            self._validator = ValueValidator(
                self._fields,
                default_country_code=self._workspace.default_country_code,
                currency=self._workspace.currency,
                timezone=self._workspace.timezone,
                options_by_field=grouped,
            )
        return self._validator

    async def _identity_key(self) -> str:
        """The key of the field this workspace identifies leads by.

        Falls back to the first PHONE-typed field only if the setting is unset,
        which provisioning makes impossible — but a workspace created before
        M2 landed would otherwise be unusable.
        """
        await self._load_schema()
        identity_id = self._workspace.identity_field_id
        for field in self._fields:
            if field.id == identity_id:
                return field.key
        raise api_error(
            409,
            "no_identity_field",
            "This workspace has no lead identity field configured",
        )

    # --- reads -------------------------------------------------------------

    def _visibility_clause(self) -> Any:
        """The manager-sees-their-reports rule, applied to leads.

        Resolved in the scoping layer (M1) into `visible_membership_ids`; this
        is the one place it is turned into a predicate. An unassigned lead is
        visible to everyone who can see the workspace — it belongs to nobody
        yet, and hiding it would mean new leads vanished until assigned.
        """
        if self._sees_all:
            return None
        return Lead.assignee_id.in_(self._visible) | Lead.assignee_id.is_(None)

    async def get_lead(self, lead_id: uuid.UUID) -> Lead:
        statement = (
            self._session.select(Lead)
            .where(Lead.id == lead_id, Lead.deleted_at.is_(None))
            .options(selectinload(Lead.actions))
            .limit(1)
        )
        clause = self._visibility_clause()
        if clause is not None:
            statement = statement.where(clause)

        rows = await self._session.execute(statement)
        lead: Lead | None = rows.scalar_one_or_none()
        if lead is None:
            # Absent, deleted, another workspace's, or outside this caller's
            # visibility — all indistinguishable, by design.
            raise not_found("Lead")
        return lead

    async def list_leads(
        self, *, limit: int, offset: int, search: str | None = None
    ) -> tuple[Sequence[Lead], int]:
        """Server-paginated (architecture rule 9). Never returns actions (rule 6)."""
        statement = self._session.select(Lead).where(Lead.deleted_at.is_(None))
        clause = self._visibility_clause()
        if clause is not None:
            statement = statement.where(clause)
        if search:
            statement = statement.where(Lead.identity_value.ilike(f"%{search}%"))

        total_result = await self._session.execute(
            select(func.count()).select_from(statement.subquery())
        )
        rows = await self._session.execute(
            statement.order_by(Lead.created_at.desc()).limit(limit).offset(offset)
        )
        return rows.scalars().all(), int(total_result.scalar_one())

    async def project(self, lead: Lead) -> dict[str, Any]:
        """The API representation, View-projected.

        The single place a lead becomes JSON. Every endpoint returns this, so
        there is no path by which a non-View field reaches a response.
        """
        validator = await self._load_schema()
        visible = self._projection.project_values(lead.values or {})

        by_id = {f.id: f for f in self._fields}
        h1 = by_id.get(self._workspace.primary_field_1_id or uuid.uuid4())
        h2 = by_id.get(self._workspace.primary_field_2_id or uuid.uuid4())

        return {
            "id": str(lead.id),
            "identity_value": lead.identity_value,
            "primary": {
                "h1": visible.get(h1.key) if h1 else None,
                "h2": visible.get(h2.key) if h2 else None,
            },
            "stage_id": str(lead.stage_id) if lead.stage_id else None,
            "lost_reason_id": str(lead.lost_reason_id) if lead.lost_reason_id else None,
            "assignee_id": str(lead.assignee_id) if lead.assignee_id else None,
            "rating": lead.rating,
            "score": lead.score,
            "values": visible,
            # Option labels for the visible subset only — decorating a field the
            # caller cannot see would leak it through the label.
            "labels": validator.project_labels(visible),
            "last_action_at": (lead.last_action_at.isoformat() if lead.last_action_at else None),
            "created_at": lead.created_at.isoformat(),
        }

    # --- writes ------------------------------------------------------------

    async def create_lead(
        self,
        *,
        values: Mapping[str, Any],
        stage_id: uuid.UUID | None = None,
        assignee_id: uuid.UUID | None = None,
        rating: int | None = None,
    ) -> tuple[Lead, ActionWriter]:
        """Create a lead, its changeset and its `LEAD_CREATED` action together."""
        validator = await self._load_schema()
        identity_key = await self._identity_key()

        self._write_filter.check(values, known_keys=validator.field_keys)

        try:
            validated = validator.validate(dict(values), is_create=True)
        except FieldValidationError as exc:
            raise api_error(
                422, "invalid_values", "One or more values are invalid", fields=exc.errors
            ) from exc

        identity = validated.values.get(identity_key)
        if identity in (None, ""):
            raise api_error(
                422,
                "identity_required",
                "A lead needs a value for the workspace's identity field",
                field=identity_key,
            )

        await self._assert_identity_free(str(identity))

        stage = await self._resolve_initial_stage(stage_id)
        if assignee_id is not None:
            await self._assert_member(assignee_id)

        lead = Lead(
            identity_value=str(identity),
            values=validated.values,
            stage_id=stage.id if stage else None,
            assignee_id=assignee_id,
            rating=rating,
            created_by_id=self._actor_id,
        )
        self._session.add(lead)
        await self._session.flush()

        writer = ActionWriter(self._session, actor_id=self._actor_id)
        await writer.open_changeset(
            source=ChangesetSource.SINGLE_EDIT,
            summary=f"Created lead {identity}",
            lead_count=1,
        )
        writer.record_created(lead)
        if assignee_id is not None:
            writer.record_assignment_change(lead, old_assignee_id=None, new_assignee_id=assignee_id)
        await self._session.flush()
        return lead, writer

    async def update_lead(
        self,
        lead_id: uuid.UUID,
        *,
        values: Mapping[str, Any] | None = None,
        stage_id: uuid.UUID | None = None,
        lost_reason_id: uuid.UUID | None = None,
        assignee_id: uuid.UUID | None = None,
        rating: int | None = None,
        unset: frozenset[str] = frozenset(),
    ) -> tuple[Lead, ActionWriter]:
        """Apply a change and append every action it implies, atomically.

        `unset` names keys the caller explicitly cleared, so "set to null" is
        distinguishable from "not mentioned" — a PATCH must not wipe fields it
        never talked about.
        """
        lead = await self.get_lead(lead_id)
        validator = await self._load_schema()

        writer = ActionWriter(self._session, actor_id=self._actor_id)
        await writer.open_changeset(
            source=ChangesetSource.SINGLE_EDIT,
            summary=f"Updated lead {lead.identity_value}",
            lead_count=1,
        )

        deltas: list[FieldDelta] = []
        if values or unset:
            payload = dict(values or {})
            for key in unset:
                payload[key] = None

            self._write_filter.check(payload, known_keys=validator.field_keys)
            try:
                validated = validator.validate(payload, is_create=False, existing=lead.values or {})
            except FieldValidationError as exc:
                raise api_error(
                    422, "invalid_values", "One or more values are invalid", fields=exc.errors
                ) from exc

            by_key = {f.key: f for f in self._fields}
            merged = dict(lead.values or {})
            for key, new_value in validated.values.items():
                old_value = merged.get(key)
                if old_value == new_value:
                    continue
                merged[key] = new_value
                deltas.append(
                    FieldDelta(
                        field_key=key,
                        label=by_key[key].label if key in by_key else key,
                        old=old_value,
                        new=new_value,
                    )
                )
            lead.values = merged

            identity_key = await self._identity_key()
            if identity_key in validated.values:
                new_identity = validated.values[identity_key]
                if new_identity and str(new_identity) != lead.identity_value:
                    await self._assert_identity_free(str(new_identity))
                    lead.identity_value = str(new_identity)

        if deltas:
            writer.record_field_changes(lead, deltas)

        if stage_id is not None and stage_id != lead.stage_id:
            stage = await self._session.get(Stage, stage_id)
            if stage is None or stage.is_archived:
                raise not_found("Stage")

            resolved_reason = await self._resolve_lost_reason(stage, lost_reason_id)
            old_stage_id = lead.stage_id
            lead.stage_id = stage.id
            lead.lost_reason_id = resolved_reason
            writer.record_stage_change(
                lead,
                old_stage_id=old_stage_id,
                new_stage_id=stage.id,
                lost_reason_id=resolved_reason,
            )

        if assignee_id is not None and assignee_id != lead.assignee_id:
            await self._assert_member(assignee_id)
            old_assignee = lead.assignee_id
            lead.assignee_id = assignee_id
            writer.record_assignment_change(
                lead, old_assignee_id=old_assignee, new_assignee_id=assignee_id
            )

        if rating is not None and rating != lead.rating:
            old_rating = lead.rating
            lead.rating = rating
            writer.record_rating_change(lead, old_rating=old_rating, new_rating=rating)

        await self._session.flush()
        return lead, writer

    async def soft_delete(self, lead_id: uuid.UUID) -> Lead:
        """Soft delete only (architecture rule 13). Leads never hard-delete."""
        lead = await self.get_lead(lead_id)
        lead.deleted_at = dt.datetime.now(dt.UTC)
        await self._session.flush()
        return lead

    async def list_actions(
        self, lead_id: uuid.UUID, *, limit: int, offset: int
    ) -> Sequence[Action]:
        """The timeline. Reached only through a lead the caller can already see."""
        await self.get_lead(lead_id)
        rows = await self._session.execute(
            self._session.select(Action)
            .where(Action.lead_id == lead_id)
            .order_by(Action.performed_at.desc())
            .limit(limit)
            .offset(offset)
        )
        actions: Sequence[Action] = rows.scalars().all()
        return actions

    # --- internals ---------------------------------------------------------

    async def _assert_identity_free(self, identity: str) -> None:
        rows = await self._session.execute(
            self._session.select(Lead)
            .where(Lead.identity_value == identity, Lead.deleted_at.is_(None))
            .limit(1)
        )
        if rows.scalar_one_or_none() is not None:
            raise conflict(
                "duplicate_identity",
                f"A lead with identity {identity!r} already exists in this workspace",
                identity=identity,
            )

    async def _assert_member(self, membership_id: uuid.UUID) -> None:
        member = await self._session.get(Membership, membership_id)
        if member is None:
            raise not_found("Member")

    async def _resolve_initial_stage(self, stage_id: uuid.UUID | None) -> Stage | None:
        if stage_id is not None:
            stage = await self._session.get(Stage, stage_id)
            if stage is None or stage.is_archived:
                raise not_found("Stage")
            return stage
        rows = await self._session.execute(
            self._session.select(Stage)
            .where(Stage.kind == StageKind.INITIAL, Stage.is_archived.is_(False))
            .limit(1)
        )
        initial: Stage | None = rows.scalar_one_or_none()
        return initial

    async def _resolve_lost_reason(
        self, stage: Stage, lost_reason_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        """Entering LOST requires a reason; leaving clears it.

        PROMPTS.md M5 states both halves. Clearing on the way out matters:
        a lead reopened from Lost that kept its reason would show up in "why we
        lose deals" forever.
        """
        if stage.kind is not StageKind.LOST:
            return None

        if lost_reason_id is not None:
            reason = await self._session.get(LostReason, lost_reason_id)
            if reason is None or reason.is_archived:
                raise not_found("Lost reason")
            return reason.id

        rows = await self._session.execute(
            self._session.select(LostReason)
            .where(LostReason.is_default.is_(True), LostReason.is_archived.is_(False))
            .limit(1)
        )
        default: LostReason | None = rows.scalar_one_or_none()
        if default is None:
            raise api_error(
                422,
                "lost_reason_required",
                "Moving a lead to the lost stage requires a lost reason",
            )
        return default.id
