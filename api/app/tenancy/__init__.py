"""Workspace scoping.

Three layers, deliberately redundant, because a tenancy leak is not a bug you
get to fix after it happens:

1. **Type level** — tenant repositories accept `ScopedSession`, never
   `AsyncSession`. A call site without a workspace scope does not type-check.
2. **Query level** — `ScopedSession` builds selects that already carry the
   `workspace_id` predicate, and its `get()` matches on id *and* workspace.
3. **ORM level** — a `do_orm_execute` listener applies `with_loader_criteria`
   to every `TenantModel` in every query issued through a scoped session,
   including relationship loads. Forgetting the filter in a hand-written query
   is therefore not sufficient to leak.

`WorkspaceScope` also resolves the team-hierarchy visibility rule once, so no
endpoint has to reimplement "a manager sees their reports' leads".
"""

from __future__ import annotations

from app.tenancy.scoping import (
    WorkspaceScope,
    require_workspace,
    require_workspace_admin,
)
from app.tenancy.session import ScopedSession

__all__ = [
    "ScopedSession",
    "WorkspaceScope",
    "require_workspace",
    "require_workspace_admin",
]
