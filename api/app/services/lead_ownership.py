"""How many open leads does a member hold, and who takes them over?

Deactivating a rep must never orphan their pipeline. That rule belongs in M1
because it governs the member lifecycle, but the `leads` table it queries
arrives in M5. This module is the seam between the two.

M1 registers `NullLeadOwnership`, which reports zero open leads — correct, since
in M1 there are none. M5 registers the real implementation and the deactivate
endpoint starts refusing with `409 reassignment_required` without any change to
the endpoint itself.

The protocol is deliberately narrow: two questions, no lead vocabulary leaking
into the members service.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import Select, func, or_, select

from app.models.enums import ChangesetSource, StageKind
from app.models.lead import Lead
from app.models.pipeline import Stage
from app.services.actions import ActionWriter
from app.tenancy.session import ScopedSession

__all__ = [
    "DatabaseLeadOwnership",
    "LeadOwnership",
    "NullLeadOwnership",
    "get_lead_ownership",
    "set_lead_ownership",
]


class LeadOwnership(Protocol):
    """Read and transfer the open pipeline a membership holds."""

    async def count_open_leads(self, session: ScopedSession, membership_id: uuid.UUID) -> int:
        """Leads assigned to this member that are not in a closed stage."""
        ...

    async def reassign_open_leads(
        self,
        session: ScopedSession,
        *,
        from_membership_id: uuid.UUID,
        to_membership_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
    ) -> int:
        """Move the open pipeline across. Returns the number moved.

        M5's implementation opens a changeset and writes one
        `ASSIGNMENT_CHANGE` action per lead, like every other mutation.
        `actor_id` is the membership performing the deactivation, so the
        changeset names who moved the pipeline rather than appearing
        authorless in the edit report.
        """
        ...


class NullLeadOwnership:
    """The M1 implementation: no leads exist yet, so nobody holds any.

    Not a stub to be deleted — it is the honest answer for a deployment that
    has not reached M5, and it keeps the deactivate flow exercisable end to end
    before then.
    """

    async def count_open_leads(self, session: ScopedSession, membership_id: uuid.UUID) -> int:
        return 0

    async def reassign_open_leads(
        self,
        session: ScopedSession,
        *,
        from_membership_id: uuid.UUID,
        to_membership_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
    ) -> int:
        return 0


def _open_leads_of(session: ScopedSession, membership_id: uuid.UUID) -> Select[tuple[Lead]]:
    """Leads that still count as pipeline for this member.

    Open means assigned to them, not soft-deleted, and *not* parked in a WON or
    LOST stage — a closed lead is history, so the rep who won it can be
    deactivated without anyone taking it over.

    `stage_id` is nullable (`ON DELETE SET NULL`), and a lead with no stage is
    open: it has certainly not been won or lost. That case needs saying out
    loud because `NULL NOT IN (...)` is `NULL`, not `TRUE`, so a bare `NOT IN`
    would drop exactly the stageless leads and re-open the orphaning hole this
    check exists to close.
    """
    closed_stage_ids = select(Stage.id).where(Stage.kind.in_((StageKind.WON, StageKind.LOST)))
    return (
        session.select(Lead)
        .where(
            Lead.assignee_id == membership_id,
            Lead.deleted_at.is_(None),
            or_(Lead.stage_id.is_(None), Lead.stage_id.not_in(closed_stage_ids)),
        )
        .order_by(Lead.created_at)
    )


class DatabaseLeadOwnership:
    """The real implementation, against the `leads` table M5 created.

    Registered at startup, which is what turns the `409 reassignment_required`
    path from a shape into a guarantee.

    No projection or write filter here, deliberately. Rule 3 governs reads that
    return customer field values; this returns an integer, and the transfer
    moves `assignee_id` — a built-in column, not a JSONB field value. There is
    nothing for `FieldProjectionService` to remove. The visibility clause is
    likewise not applied: this is a pipeline-integrity check about the member
    being deactivated, not a lead read on behalf of the caller, and an admin who
    could only see part of the pipeline would otherwise orphan the rest.
    """

    async def count_open_leads(self, session: ScopedSession, membership_id: uuid.UUID) -> int:
        result = await session.execute(
            select(func.count()).select_from(_open_leads_of(session, membership_id).subquery())
        )
        return int(result.scalar_one())

    async def reassign_open_leads(
        self,
        session: ScopedSession,
        *,
        from_membership_id: uuid.UUID,
        to_membership_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
    ) -> int:
        """Move the open pipeline across as one undoable batch.

        One changeset, one `ASSIGNMENT_CHANGE` per lead carrying both ids
        (rule 5b), so M7 can undo a mistaken deactivation and M6 can answer
        "which leads moved off this rep". Nothing is committed — `deactivate`
        owns the transaction, so the transfer and the deactivation land
        together or not at all.
        """
        rows = await session.execute(_open_leads_of(session, from_membership_id))
        leads = list(rows.scalars().all())
        if not leads:
            # No empty changeset: the edit report should not carry rows for
            # batches that moved nothing.
            return 0

        writer = ActionWriter(session, actor_id=actor_id)
        await writer.open_changeset(
            source=ChangesetSource.DISTRIBUTION,
            summary=f"Reassigned {len(leads)} open leads on deactivation",
            lead_count=len(leads),
        )
        for lead in leads:
            writer.record_assignment_change(
                lead,
                old_assignee_id=lead.assignee_id,
                new_assignee_id=to_membership_id,
            )
            lead.assignee_id = to_membership_id
        await session.flush()
        return len(leads)


_implementation: LeadOwnership = NullLeadOwnership()


def get_lead_ownership() -> LeadOwnership:
    return _implementation


def set_lead_ownership(implementation: LeadOwnership) -> None:
    """Swap the implementation. Called by M5 at import time, and by tests."""
    global _implementation  # one process-wide seam, set at startup
    _implementation = implementation
