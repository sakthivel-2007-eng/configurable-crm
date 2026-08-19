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

from app.tenancy.session import ScopedSession

__all__ = ["LeadOwnership", "NullLeadOwnership", "get_lead_ownership", "set_lead_ownership"]


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
    ) -> int:
        """Move the open pipeline across. Returns the number moved.

        M5's implementation opens a changeset and writes one
        `ASSIGNMENT_CHANGE` action per lead, like every other mutation.
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
    ) -> int:
        return 0


_implementation: LeadOwnership = NullLeadOwnership()


def get_lead_ownership() -> LeadOwnership:
    return _implementation


def set_lead_ownership(implementation: LeadOwnership) -> None:
    """Swap the implementation. Called by M5 at import time, and by tests."""
    global _implementation  # one process-wide seam, set at startup
    _implementation = implementation
