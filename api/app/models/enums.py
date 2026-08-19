"""Product enums.

Per docs/01-data-model.md §1: the only enums in this database are *product*
concepts. Every business concept is a row. If you are about to add something
like `ProductType` or `ApplicationStatus` here, it belongs in a table.

M1 owns one enum. The rest (`stage_kind`, `lead_field_type`, ...) arrive with
the milestones that need them.
"""

from __future__ import annotations

import enum

__all__ = ["AvailabilityStatus"]


class AvailabilityStatus(enum.StrEnum):
    """Whether a member is eligible to receive work.

    WORKING is the only status the assignment engine (M8) will hand a lead to
    when a rule sets `skip_unavailable`. ON_LEAVE is temporary and self-service;
    INACTIVE is set by deactivation and requires a licence to reverse.
    """

    WORKING = "WORKING"
    ON_LEAVE = "ON_LEAVE"
    INACTIVE = "INACTIVE"
