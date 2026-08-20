"""`ScopedSession` — a database session that cannot address another tenant.

This is the type the whole product hangs off. Tenant repositories take a
`ScopedSession`; they never take an `AsyncSession`. Because `ScopedSession`
cannot be constructed without a `workspace_id`, "I forgot to scope this query"
stops being a class of bug that reaches review.

The underlying `AsyncSession` is private. That is not politeness — exposing it
would hand every caller a way around all three enforcement layers.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.event import listens_for
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, with_loader_criteria
from sqlalchemy.sql import Executable

from app.models.mixins import Base, TenantModel, TenantScoped

__all__ = ["ScopedSession", "WorkspaceMismatchError"]

#: Anything carrying `workspace_id` — including composite-keyed tables.
TenantT = TypeVar("TenantT", bound=TenantScoped)
#: Surrogate-keyed tenant tables. `get()` needs `.id`, which a
#: composite-keyed row does not have, so by-id lookup is narrower than
#: select/add by design.
KeyedT = TypeVar("KeyedT", bound=TenantModel)

# Key under which the active workspace is stashed on the SQLAlchemy session's
# `info` dict, where the ORM execute listener below can find it.
_SCOPE_KEY = "crm_workspace_id"


class WorkspaceMismatchError(RuntimeError):
    """Raised when an object from another workspace enters a scoped session.

    A programming error, not a user error — it means some code loaded an object
    outside the scope and tried to write it inside one. It should never surface
    as an HTTP response.
    """


@listens_for(AsyncSession.sync_session_class, "do_orm_execute")
def _apply_workspace_criteria(state: ORMExecuteState) -> None:
    """Force `workspace_id = :scope` onto every tenant entity in every query.

    Targets `TenantScoped` rather than `TenantModel` so composite-keyed tenant
    tables (`template_field_grants`) are covered too.

    Applies to relationship loads and lazy loads too, which is the part a
    hand-written `WHERE` clause always misses. Sessions with no scope in their
    `info` (the auth/global path) are left alone — they may only touch
    non-tenant tables, which this criteria would not match anyway.
    """
    workspace_id = state.session.info.get(_SCOPE_KEY)
    if workspace_id is None or not state.is_select:
        return

    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.workspace_id == workspace_id,
            include_aliases=True,
        )
    )


class ScopedSession:
    """An `AsyncSession` bound to exactly one workspace.

    Every read is filtered to that workspace and every write is stamped with
    it. There is no method that lets a caller name a different one.
    """

    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        self._session = session
        self._workspace_id = workspace_id
        session.info[_SCOPE_KEY] = workspace_id

    @property
    def workspace_id(self) -> uuid.UUID:
        return self._workspace_id

    # --- reads -------------------------------------------------------------

    def select(self, model: type[TenantT]) -> Select[tuple[TenantT]]:
        """A `SELECT` already filtered to this workspace.

        Accepts only `TenantModel` subclasses. Passing a non-tenant model is a
        type error, which is the point: `Workspace` and `User` are read through
        the global repositories, not through a scoped session.
        """
        return select(model).where(model.workspace_id == self._workspace_id)

    async def get(self, model: type[KeyedT], entity_id: uuid.UUID) -> KeyedT | None:
        """Fetch by id *within this workspace*.

        Returns `None` for another workspace's id exactly as it does for one
        that does not exist — the caller cannot tell the difference, and so
        neither can an attacker probing for valid ids.
        """
        result = await self._session.execute(
            self.select(model).where(model.id == entity_id).limit(1)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        model: type[TenantT],
        *,
        limit: int,
        offset: int,
        order_by: Any = None,  # any ORM column/UnaryExpression
    ) -> tuple[Sequence[TenantT], int]:
        """Paginated read plus the total, both scoped.

        All list endpoints are server-paginated (architecture rule 9), so the
        limit is required rather than defaulted here.
        """
        statement = self.select(model)
        if order_by is not None:
            statement = statement.order_by(order_by)

        rows = await self._session.execute(statement.limit(limit).offset(offset))
        total = await self._session.execute(
            select(func.count()).select_from(self.select(model).subquery())
        )
        return rows.scalars().all(), int(total.scalar_one())

    async def execute(self, statement: Executable) -> Any:  # SQLAlchemy Result
        """Run a statement the ORM criteria still applies to.

        Use for joins and aggregates that `select()` cannot express. The
        loader criteria above still stamps every tenant entity involved, so
        this is not an escape hatch — it is the same guarantee, less sugar.
        """
        return await self._session.execute(statement)

    # --- writes ------------------------------------------------------------

    def add(self, instance: TenantScoped) -> None:
        """Stage an insert, stamping the workspace.

        An instance arriving with a *different* workspace already set is a
        programming error and raises rather than being silently corrected —
        silently correcting it would write one tenant's data under another's id.
        """
        current = getattr(instance, "workspace_id", None)
        if current is not None and current != self._workspace_id:
            raise WorkspaceMismatchError(
                f"{type(instance).__name__} belongs to workspace {current}, "
                f"session is scoped to {self._workspace_id}"
            )
        instance.workspace_id = self._workspace_id
        self._session.add(instance)

    def add_all(self, instances: Sequence[TenantScoped]) -> None:
        for instance in instances:
            self.add(instance)

    def add_global(self, instance: Base) -> None:
        """Stage a non-tenant row (a `User`) from inside a scoped flow.

        Member invitation genuinely needs this: a user account is global, and
        the membership that scopes it is created in the same transaction.
        Passing a `TenantModel` raises — this is not a way to write tenant data
        without a workspace, and refusing loudly is what keeps it from becoming
        one.
        """
        if isinstance(instance, TenantScoped):
            raise WorkspaceMismatchError(
                f"{type(instance).__name__} is tenant data; use add() so it is "
                f"stamped with workspace {self._workspace_id}"
            )
        self._session.add(instance)

    # No `delete()`. Soft delete is the rule everywhere (architecture rule 13):
    # config entities archive, leads and actions never hard-delete. Adding a
    # hard-delete helper here would be the first crack in that.

    async def flush(self) -> None:
        await self._session.flush()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def refresh(self, instance: object) -> None:
        await self._session.refresh(instance)
