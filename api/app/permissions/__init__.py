"""Field-level permissions (M4).

The two services in `projection` are the chokepoints CLAUDE.md rules 3 and 4
require: one implementation of "which fields may this caller read" and one of
"which may they write". Everything that touches lead data goes through them.

`capabilities` is the validated shape of `permission_templates.capabilities` —
the 10 Access groups and 3 View groups, with the nine proposed ones marked.
"""

from __future__ import annotations

from app.permissions.capabilities import (
    ACCESS_GROUPS,
    PROPOSED_GROUPS,
    VIEW_GROUPS,
    Capabilities,
)
from app.permissions.projection import (
    FieldGrants,
    FieldProjectionService,
    FieldWriteFilter,
    load_grants,
)

__all__ = [
    "ACCESS_GROUPS",
    "PROPOSED_GROUPS",
    "VIEW_GROUPS",
    "Capabilities",
    "FieldGrants",
    "FieldProjectionService",
    "FieldWriteFilter",
    "load_grants",
]
