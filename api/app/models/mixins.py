"""Base classes and mixins.

`Base` re-exports the declarative base from `app.db`. `TimestampMixin` adds
`created_at`/`updated_at` with server defaults. `TenantModel` declares
`workspace_id` and its FK to `workspaces(id) ON DELETE CASCADE`, so every table
that inherits it is tenant-scoped by construction.

If you find yourself defining a table with customer data and NOT inheriting
`TenantModel`, stop and reconsider. The only tables that skip it are `users`,
`workspaces`, and the auth infrastructure (`refresh_tokens`) — everything else
is per-tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

__all__ = ["Base", "TenantModel", "TenantScoped", "TimestampMixin"]


class TimestampMixin:
    """`created_at` and `updated_at`, both `timestamptz` and server-defaulted."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TenantScoped(Base):
    """Anything that carries `workspace_id`, whatever its primary key.

    Split out from `TenantModel` because a handful of tables are legitimately
    keyed on something other than a surrogate id — `template_field_grants` is
    `PRIMARY KEY (template_id, field_id, grant)` per docs/01-data-model.md §3.5,
    since the row *is* the fact.

    The split matters for isolation, not tidiness: `ScopedSession`'s loader
    criteria targets **this** class, so a composite-keyed tenant table is
    auto-filtered exactly like every other one. Before the split it would have
    fallen outside the criteria and relied on each query remembering its own
    `WHERE workspace_id` — the discipline this architecture exists to remove.
    """

    __abstract__ = True

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class TenantModel(TenantScoped, TimestampMixin):
    """Base for every table that holds tenant data.

    Guarantees `workspace_id` at the schema level. Subclasses should still
    include `workspace_id` in every unique constraint and index that touches
    tenant data — Postgres does not know a query is "for a tenant" unless the
    column is in the index.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
