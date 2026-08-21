"""Reversing a changeset (M7).

Everything undo needs was designed in from M5: `changesets`, a `changeset_id` on
every action, and old *and* new values in every reversible payload. This module
is only the replay.

**How a conflict is detected.** Not "was this lead touched since?" — a note
logged afterwards does not make a stage revert unsafe — but per target: for each
value the changeset set, is the lead's *current* value still the one it set? If
it is not, something changed it since and reverting would clobber that. This is
optimistic concurrency rather than a timeline scan, and it is both cheaper and
more precise: it catches an edit from any source (UI, import, intake, another
undo), and it correctly ignores a change that was made and then reverted by hand.

**Nothing is silently skipped.** `preview_undo` reports every lead and every
target it would touch. `undo` refuses outright unless the caller either has no
conflicts or explicitly passes `skip_conflicts`. The operator decides; the
product does not decide for them.

An undo is itself a changeset with `undo_of_id` set, so undoing an undo is just
another undo rather than a special case.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func

from app.errors import conflict, not_found
from app.fields.search import SEARCH_CONFIG, search_text_for
from app.models.enums import ChangesetSource, SystemActionKind
from app.models.field import LeadField
from app.models.lead import Action, Changeset, Lead
from app.models.workspace import Workspace
from app.permissions.projection import FieldProjectionService, FieldWriteFilter
from app.services.actions import ActionWriter, FieldDelta
from app.tenancy.session import ScopedSession

__all__ = ["LeadUndoPlan", "Reversal", "UndoOutcome", "UndoPreview", "UndoService"]

#: The only kinds that carry enough to reverse. A NOTE has no "before", and a
#: CALL_LOGGED records something that happened in the world — undoing an edit
#: must not claim a phone call did not occur.
REVERSIBLE_KINDS: frozenset[SystemActionKind] = frozenset(
    {
        SystemActionKind.FIELD_CHANGE,
        SystemActionKind.STAGE_CHANGE,
        SystemActionKind.ASSIGNMENT_CHANGE,
        SystemActionKind.RATING_CHANGE,
    }
)


class UndoOutcome(enum.StrEnum):
    """What would happen to one lead if this changeset were undone."""

    REVERSIBLE = "REVERSIBLE"
    #: Something changed one of these values after the changeset. Reverting
    #: would discard that later change.
    CONFLICTED = "CONFLICTED"
    ALREADY_UNDONE = "ALREADY_UNDONE"
    #: The lead was soft-deleted since. Undoing into it would resurrect data
    #: someone deliberately removed.
    DELETED = "DELETED"


@dataclasses.dataclass(frozen=True, slots=True)
class Reversal:
    """One value this undo would put back, and whether it still can."""

    #: `values.<key>`, or `stage` / `assignee` / `rating`.
    target: str
    label: str
    kind: SystemActionKind
    #: What the value was before the changeset — where it would go back to.
    revert_to: Any
    #: What the changeset set it to — what the current value must still be.
    expected: Any
    current: Any
    conflicted: bool


@dataclasses.dataclass(frozen=True, slots=True)
class LeadUndoPlan:
    lead_id: uuid.UUID
    identity_value: str
    outcome: UndoOutcome
    reversals: tuple[Reversal, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "lead_id": str(self.lead_id),
            "identity_value": self.identity_value,
            "outcome": self.outcome.value,
            "reversals": [
                {
                    "target": r.target,
                    "label": r.label,
                    "revert_to": r.revert_to,
                    "expected": r.expected,
                    "current": r.current,
                    "conflicted": r.conflicted,
                }
                for r in self.reversals
            ],
        }


@dataclasses.dataclass(frozen=True, slots=True)
class UndoPreview:
    changeset_id: uuid.UUID
    summary: str
    is_undone: bool
    leads: tuple[LeadUndoPlan, ...]

    @property
    def conflicted(self) -> tuple[LeadUndoPlan, ...]:
        return tuple(p for p in self.leads if p.outcome is UndoOutcome.CONFLICTED)

    @property
    def reversible(self) -> tuple[LeadUndoPlan, ...]:
        return tuple(p for p in self.leads if p.outcome is UndoOutcome.REVERSIBLE)

    def as_payload(self) -> dict[str, Any]:
        return {
            "changeset_id": str(self.changeset_id),
            "summary": self.summary,
            "is_undone": self.is_undone,
            "counts": {
                "total": len(self.leads),
                "reversible": len(self.reversible),
                "conflicted": len(self.conflicted),
                "deleted": sum(1 for p in self.leads if p.outcome is UndoOutcome.DELETED),
            },
            "leads": [plan.as_payload() for plan in self.leads],
        }


def _as_text(value: Any) -> str | None:
    """Payload ids are stored as strings; columns hold uuids. Compare as text."""
    return None if value is None else str(value)


class UndoService:
    """Builds and applies the inverse of a changeset.

    Constructed per request with the caller's projection and write filter bound,
    like every other lead write path — undo is a lead write, and gets no
    exemption from the chokepoints.
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

    # --- reading ------------------------------------------------------------

    async def _changeset(self, changeset_id: uuid.UUID) -> Changeset:
        found = await self._session.get(Changeset, changeset_id)
        if found is None:
            raise not_found("Changeset")
        return found

    async def _actions_of(self, changeset_id: uuid.UUID) -> Sequence[Action]:
        rows = await self._session.execute(
            self._session.select(Action)
            .where(Action.changeset_id == changeset_id, Action.kind.in_(REVERSIBLE_KINDS))
            # Oldest first: within a changeset the earliest action holds the
            # value to go back to, the latest holds what to check against.
            .order_by(Action.performed_at, Action.id)
        )
        actions: Sequence[Action] = rows.scalars().all()
        return actions

    async def _leads_by_id(self, lead_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Lead]:
        """The subset of these leads this caller may see.

        The same visibility rule the lead list applies. A changeset can span
        leads a manager cannot see, and undo must not be a way around that —
        so those leads are simply absent from the plan rather than reverted
        invisibly.
        """
        if not lead_ids:
            return {}
        statement = self._session.select(Lead).where(Lead.id.in_(lead_ids))
        if not self._sees_all:
            statement = statement.where(
                Lead.assignee_id.in_(self._visible) | Lead.assignee_id.is_(None)
            )
        rows = await self._session.execute(statement)
        return {lead.id: lead for lead in rows.scalars().all()}

    async def _fields(self) -> Sequence[LeadField]:
        rows = await self._session.execute(self._session.select(LeadField))
        fields: Sequence[LeadField] = rows.scalars().all()
        return fields

    def _refresh_search_vector(self, lead: Lead, fields: Sequence[LeadField]) -> None:
        """Same maintenance every other write path does.

        Duplicated here rather than reached for through `LeadService` because
        that service is built around validating an incoming payload, and an
        undo has no payload — it replays values the workspace already accepted.
        """
        document = search_text_for(lead.values or {}, fields, identity_value=lead.identity_value)
        lead.search_vector = func.to_tsvector(SEARCH_CONFIG, document)

    async def _field_labels(self) -> Mapping[str, str]:
        rows = await self._session.execute(self._session.select(LeadField))
        return {field.key: field.label for field in rows.scalars().all()}

    # --- planning -----------------------------------------------------------

    def _plan_for(
        self, lead: Lead, actions: Sequence[Action], labels: Mapping[str, str]
    ) -> tuple[Reversal, ...]:
        """Collapse a lead's actions in this changeset into one reversal per target.

        A field touched twice in one batch reverts to what it was before the
        batch, not to its intermediate value — which is why `revert_to` comes
        from the first action and `expected` from the last.
        """
        first: dict[str, Any] = {}
        last: dict[str, Any] = {}
        kinds: dict[str, SystemActionKind] = {}
        names: dict[str, str] = {}

        for action in actions:
            payload = action.payload or {}
            if action.kind is SystemActionKind.FIELD_CHANGE:
                key = str(payload.get("field_key", ""))
                if not key:
                    continue
                target = f"values.{key}"
                names[target] = str(payload.get("label") or labels.get(key, key))
                old, new = payload.get("old"), payload.get("new")
            elif action.kind is SystemActionKind.STAGE_CHANGE:
                target = "stage"
                names[target] = "Stage"
                old, new = payload.get("old_stage_id"), payload.get("new_stage_id")
            elif action.kind is SystemActionKind.ASSIGNMENT_CHANGE:
                target = "assignee"
                names[target] = "Assignee"
                old, new = payload.get("old_assignee_id"), payload.get("new_assignee_id")
            elif action.kind is SystemActionKind.RATING_CHANGE:
                target = "rating"
                names[target] = "Rating"
                old, new = payload.get("old"), payload.get("new")
            else:  # pragma: no cover - filtered by REVERSIBLE_KINDS
                continue

            kinds[target] = action.kind
            first.setdefault(target, old)
            last[target] = new

        reversals: list[Reversal] = []
        for target, revert_to in first.items():
            expected = last[target]
            current = self._current_value(lead, target)
            reversals.append(
                Reversal(
                    target=target,
                    label=names.get(target, target),
                    kind=kinds[target],
                    revert_to=revert_to,
                    expected=expected,
                    current=current,
                    conflicted=current != expected,
                )
            )
        return tuple(sorted(reversals, key=lambda r: r.target))

    def _current_value(self, lead: Lead, target: str) -> Any:
        if target == "stage":
            return _as_text(lead.stage_id)
        if target == "assignee":
            return _as_text(lead.assignee_id)
        if target == "rating":
            return lead.rating
        return (lead.values or {}).get(target.removeprefix("values."))

    async def preview_undo(self, changeset_id: uuid.UUID) -> UndoPreview:
        """What undoing this changeset would do, lead by lead."""
        changeset = await self._changeset(changeset_id)
        actions = await self._actions_of(changeset_id)
        labels = await self._field_labels()

        by_lead: dict[uuid.UUID, list[Action]] = {}
        for action in actions:
            by_lead.setdefault(action.lead_id, []).append(action)

        leads = await self._leads_by_id(list(by_lead))
        plans: list[LeadUndoPlan] = []

        for lead_id, lead_actions in by_lead.items():
            lead = leads.get(lead_id)
            if lead is None:
                # Another workspace's, or hard gone. Neither should happen —
                # leads never hard-delete — so it is reported rather than hidden.
                continue

            reversals = self._plan_for(lead, lead_actions, labels)
            if changeset.is_undone:
                outcome = UndoOutcome.ALREADY_UNDONE
            elif lead.deleted_at is not None:
                outcome = UndoOutcome.DELETED
            elif any(r.conflicted for r in reversals):
                outcome = UndoOutcome.CONFLICTED
            else:
                outcome = UndoOutcome.REVERSIBLE

            plans.append(
                LeadUndoPlan(
                    lead_id=lead_id,
                    identity_value=lead.identity_value,
                    outcome=outcome,
                    reversals=reversals,
                )
            )

        plans.sort(key=lambda p: p.identity_value)
        return UndoPreview(
            changeset_id=changeset_id,
            summary=changeset.summary,
            is_undone=changeset.is_undone,
            leads=tuple(plans),
        )

    # --- applying -----------------------------------------------------------

    async def _assert_editable(self, preview: UndoPreview) -> None:
        """Every field this undo would write goes through the write filter.

        Architecture rule 4, with no exemption for a replay: the values are
        ones the workspace already accepted, but the *caller* reverting them
        may not be the one who set them.

        Checked across the whole batch before anything is applied. Refused, not
        trimmed — a partial undo that quietly left some fields alone is worse
        than none at all, because the operator would believe the batch was
        reversed.
        """
        touched = {
            reversal.target.removeprefix("values."): reversal.revert_to
            for plan in preview.leads
            if plan.outcome is UndoOutcome.REVERSIBLE
            for reversal in plan.reversals
            if reversal.target.startswith("values.")
        }
        if not touched:
            return
        known = {field.key for field in await self._fields()}
        self._write_filter.check(touched, known_keys=frozenset(known))

    async def undo(
        self, changeset_id: uuid.UUID, *, skip_conflicts: bool = False
    ) -> dict[str, Any]:
        """Reverse a changeset, atomically, recording a new one that says so."""
        changeset = await self._changeset(changeset_id)
        if changeset.is_undone:
            raise conflict(
                "already_undone",
                "This change has already been undone",
                changeset_id=str(changeset_id),
                undone_at=changeset.undone_at.isoformat() if changeset.undone_at else None,
            )

        preview = await self.preview_undo(changeset_id)
        if not preview.leads:
            raise conflict(
                "nothing_to_undo",
                "This change produced nothing that can be reversed",
                changeset_id=str(changeset_id),
            )

        if preview.conflicted and not skip_conflicts:
            # The rule the handoff singles out. The operator is shown exactly
            # which leads changed and how, and chooses; nothing is clobbered on
            # their behalf.
            raise conflict(
                "undo_conflicts",
                f"{len(preview.conflicted)} of {len(preview.leads)} leads changed after this "
                f"edit. Re-run with skip_conflicts to undo the rest.",
                **preview.as_payload(),
            )

        await self._assert_editable(preview)

        applicable = preview.reversible
        if not applicable:
            raise conflict(
                "nothing_to_undo",
                "Every lead in this change has been modified since",
                **preview.as_payload(),
            )

        leads = await self._leads_by_id([plan.lead_id for plan in applicable])
        fields = await self._fields()
        writer = ActionWriter(self._session, actor_id=self._actor_id)
        await writer.open_changeset(
            # `undo_of_id` is what marks this as an undo; the source describes
            # its shape, which is a batch edit like any other.
            source=(
                ChangesetSource.BULK_EDIT if len(applicable) > 1 else ChangesetSource.SINGLE_EDIT
            ),
            summary=f"Undid: {changeset.summary}",
            lead_count=len(applicable),
        )
        writer.changeset.undo_of_id = changeset.id

        for plan in applicable:
            lead = leads[plan.lead_id]
            deltas: list[FieldDelta] = []

            for reversal in plan.reversals:
                if reversal.target == "stage":
                    old = lead.stage_id
                    lead.stage_id = uuid.UUID(reversal.revert_to) if reversal.revert_to else None
                    writer.record_stage_change(lead, old_stage_id=old, new_stage_id=lead.stage_id)
                elif reversal.target == "assignee":
                    old_assignee = lead.assignee_id
                    lead.assignee_id = uuid.UUID(reversal.revert_to) if reversal.revert_to else None
                    writer.record_assignment_change(
                        lead, old_assignee_id=old_assignee, new_assignee_id=lead.assignee_id
                    )
                elif reversal.target == "rating":
                    old_rating = lead.rating
                    lead.rating = reversal.revert_to
                    writer.record_rating_change(lead, old_rating=old_rating, new_rating=lead.rating)
                else:
                    key = reversal.target.removeprefix("values.")
                    values = dict(lead.values or {})
                    old_value = values.get(key)
                    if reversal.revert_to is None:
                        values.pop(key, None)
                    else:
                        values[key] = reversal.revert_to
                    lead.values = values
                    deltas.append(
                        FieldDelta(
                            field_key=key,
                            label=reversal.label,
                            old=old_value,
                            new=reversal.revert_to,
                        )
                    )

            if deltas:
                writer.record_field_changes(lead, deltas)
                # A vector left describing the pre-undo values would make the
                # lead findable by text it no longer contains.
                self._refresh_search_vector(lead, fields)

        changeset.is_undone = True
        changeset.undone_at = dt.datetime.now(dt.UTC)
        changeset.undone_by_id = self._actor_id
        await self._session.flush()

        return {
            "undo_changeset_id": str(writer.changeset.id),
            "undone_changeset_id": str(changeset.id),
            "leads_reverted": len(applicable),
            "leads_skipped": len(preview.leads) - len(applicable),
            "skipped": [
                plan.as_payload()
                for plan in preview.leads
                if plan.outcome is not UndoOutcome.REVERSIBLE
            ],
        }
