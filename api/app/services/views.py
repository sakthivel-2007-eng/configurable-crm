"""Saved filters and table layouts (M6).

The visibility rule is the whole of the interesting logic here, and it is worth
being precise about what it does and does not do.

A saved filter's `visibility` decides **who can see the filter**. It has no
bearing on **which leads the filter returns** — that is settled at run time by
the runner's own field grants and lead visibility. A shared filter is a shared
*question*, never a shared answer, so handing someone a filter can never hand
them a lead or a column they could not otherwise reach.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from app.errors import forbidden, not_found, unprocessable
from app.filters.dsl import FilterNode, validate_shape
from app.models.enums import SavedFilterVisibility
from app.models.view import SavedFilter, TableLayout
from app.models.workspace import PermissionTemplate
from app.tenancy.session import ScopedSession

__all__ = ["ViewService"]

#: A workspace that accumulates hundreds of saved filters has a navigation
#: problem, not a filtering one. Bounded so the sidebar stays usable and the
#: reorder payload stays small.
MAX_SAVED_FILTERS = 200

#: Built once. Re-validating on read rather than trusting the stored blob is
#: deliberate: a filter saved against a field the workspace has since removed
#: must fail loudly at run time, not compile into a query that quietly
#: matches nothing.
_FILTER_ADAPTER: TypeAdapter[FilterNode] = TypeAdapter(FilterNode)


class ViewService:
    """CRUD for saved filters and per-member table layouts."""

    def __init__(
        self,
        session: ScopedSession,
        *,
        membership_id: uuid.UUID,
        template_id: uuid.UUID,
        is_admin: bool,
    ) -> None:
        self._session = session
        self._membership_id = membership_id
        self._template_id = template_id
        self._is_admin = is_admin

    # --- visibility --------------------------------------------------------

    def _visible_clause(self) -> ColumnElement[bool]:
        """Which saved filters this caller may see.

        Their own personal ones, everything shared, and anything scoped to the
        template they are on. An admin sees the lot — otherwise nobody could
        tidy up a filter left behind by someone who has since left.
        """
        if self._is_admin:
            return SavedFilter.id.is_not(None)
        return or_(
            SavedFilter.visibility == SavedFilterVisibility.SHARED,
            (SavedFilter.visibility == SavedFilterVisibility.PERSONAL)
            & (SavedFilter.owner_membership_id == self._membership_id),
            (SavedFilter.visibility == SavedFilterVisibility.ROLE)
            & (SavedFilter.template_id == self._template_id),
        )

    async def list_filters(self, *, include_archived: bool = False) -> Sequence[SavedFilter]:
        statement = self._session.select(SavedFilter).where(self._visible_clause())
        if not include_archived:
            statement = statement.where(SavedFilter.is_archived.is_(False))
        rows = await self._session.execute(
            statement.order_by(SavedFilter.sort_order, SavedFilter.created_at)
        )
        found: Sequence[SavedFilter] = rows.scalars().all()
        return found

    async def get_filter(self, filter_id: uuid.UUID) -> SavedFilter:
        rows = await self._session.execute(
            self._session.select(SavedFilter)
            .where(SavedFilter.id == filter_id, self._visible_clause())
            .limit(1)
        )
        found: SavedFilter | None = rows.scalar_one_or_none()
        if found is None:
            # Another workspace's, archived out of view, or one this caller has
            # no business knowing exists — all indistinguishable, by design.
            raise not_found("Saved filter")
        return found

    def parse_definition(self, saved: SavedFilter) -> FilterNode:
        """The stored JSON, back as a typed node.

        Stored filters are re-parsed on every run rather than trusted, because
        the DSL's own shape can move under them between milestones.
        """
        try:
            return _FILTER_ADAPTER.validate_python(saved.definition)
        except ValidationError as exc:
            raise unprocessable(
                "invalid_filter",
                f"{saved.name!r} can no longer be read as a filter",
                errors=exc.errors(include_url=False),
            ) from exc

    def _assert_may_edit(self, saved: SavedFilter) -> None:
        """Authors edit their own; admins edit anyone's.

        A SHARED filter that any member could rewrite would be a shared
        footgun — one person's edit silently changes what everyone else's
        worklist means.
        """
        if self._is_admin or saved.owner_membership_id == self._membership_id:
            return
        raise forbidden(
            "not_filter_owner",
            "Only the person who created this filter, or an admin, can change it",
        )

    # --- writes ------------------------------------------------------------

    async def _validate_definition(self, definition: FilterNode) -> None:
        try:
            validate_shape(definition)
        except ValueError as exc:
            raise unprocessable("invalid_filter", str(exc)) from exc

    async def _assert_template(self, template_id: uuid.UUID) -> None:
        template = await self._session.get(PermissionTemplate, template_id)
        if template is None:
            raise not_found("Permission template")

    async def create_filter(
        self,
        *,
        name: str,
        description: str | None,
        definition: FilterNode,
        visibility: SavedFilterVisibility,
        template_id: uuid.UUID | None,
    ) -> SavedFilter:
        await self._validate_definition(definition)

        if visibility is SavedFilterVisibility.ROLE:
            if template_id is None:
                raise unprocessable(
                    "template_required", "A role-scoped filter must name a permission template"
                )
            await self._assert_template(template_id)
        elif template_id is not None:
            raise unprocessable(
                "template_not_allowed",
                "Only a role-scoped filter names a permission template",
            )

        existing = await self.list_filters(include_archived=True)
        if len(existing) >= MAX_SAVED_FILTERS:
            raise unprocessable(
                "saved_filter_limit",
                f"A workspace may keep at most {MAX_SAVED_FILTERS} saved filters",
            )

        saved = SavedFilter(
            name=name,
            description=description,
            definition=definition.model_dump(mode="json", by_alias=True),
            visibility=visibility,
            template_id=template_id,
            owner_membership_id=self._membership_id,
            sort_order=len(existing),
        )
        self._session.add(saved)
        await self._session.flush()
        return saved

    async def update_filter(
        self,
        filter_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        definition: FilterNode | None = None,
        visibility: SavedFilterVisibility | None = None,
        template_id: uuid.UUID | None = None,
        template_id_given: bool = False,
    ) -> SavedFilter:
        saved = await self.get_filter(filter_id)
        self._assert_may_edit(saved)

        if name is not None:
            saved.name = name
        if description is not None:
            saved.description = description
        if definition is not None:
            await self._validate_definition(definition)
            saved.definition = definition.model_dump(mode="json", by_alias=True)

        resolved_visibility = visibility or saved.visibility
        resolved_template = template_id if template_id_given else saved.template_id

        if resolved_visibility is SavedFilterVisibility.ROLE:
            if resolved_template is None:
                raise unprocessable(
                    "template_required", "A role-scoped filter must name a permission template"
                )
            await self._assert_template(resolved_template)
        else:
            # Dropping the template alongside the visibility keeps the check
            # constraint satisfied; leaving it would fail at flush with a
            # database error instead of this message.
            resolved_template = None

        saved.visibility = resolved_visibility
        saved.template_id = resolved_template
        await self._session.flush()
        return saved

    async def duplicate_filter(self, filter_id: uuid.UUID) -> SavedFilter:
        """Copy someone else's filter into your own.

        The copy is always PERSONAL and owned by the copier — duplicating a
        shared filter to tweak it must not quietly publish the tweak.
        """
        source = await self.get_filter(filter_id)
        existing = await self.list_filters(include_archived=True)
        copy = SavedFilter(
            name=f"{source.name} (copy)"[:80],
            description=source.description,
            definition=dict(source.definition),
            visibility=SavedFilterVisibility.PERSONAL,
            template_id=None,
            owner_membership_id=self._membership_id,
            sort_order=len(existing),
        )
        self._session.add(copy)
        await self._session.flush()
        return copy

    async def archive_filter(self, filter_id: uuid.UUID) -> SavedFilter:
        saved = await self.get_filter(filter_id)
        self._assert_may_edit(saved)
        saved.is_archived = True
        await self._session.flush()
        return saved

    async def reorder_filters(self, filter_ids: Sequence[uuid.UUID]) -> Sequence[SavedFilter]:
        """Apply an explicit order to the caller's visible filters.

        Ids outside the visible set are refused rather than ignored: silently
        dropping one would reorder a list the caller did not think they were
        reordering.
        """
        visible = {f.id: f for f in await self.list_filters()}
        unknown = [str(i) for i in filter_ids if i not in visible]
        if unknown:
            raise unprocessable(
                "unknown_filter", "The order names filters that are not visible here", ids=unknown
            )
        for position, filter_id in enumerate(filter_ids):
            visible[filter_id].sort_order = position
        await self._session.flush()
        return await self.list_filters()

    # --- layouts -----------------------------------------------------------

    async def get_layout(self, filter_id: uuid.UUID | None) -> TableLayout | None:
        """This member's columns for one filter, or their default."""
        if filter_id is not None:
            await self.get_filter(filter_id)
        rows = await self._session.execute(
            self._session.select(TableLayout)
            .where(
                TableLayout.membership_id == self._membership_id,
                TableLayout.filter_id == filter_id
                if filter_id is not None
                else TableLayout.filter_id.is_(None),
            )
            .limit(1)
        )
        layout: TableLayout | None = rows.scalar_one_or_none()
        return layout

    async def put_layout(
        self,
        filter_id: uuid.UUID | None,
        *,
        columns: Sequence[str],
        column_widths: dict[str, int],
        sort_key: str | None,
        sort_desc: bool,
    ) -> TableLayout:
        """Upsert. A layout is a preference, so saving twice is not a conflict."""
        layout = await self.get_layout(filter_id)
        if layout is None:
            layout = TableLayout(membership_id=self._membership_id, filter_id=filter_id)
            self._session.add(layout)

        layout.columns = list(columns)
        layout.column_widths = dict(column_widths)
        layout.sort_key = sort_key
        layout.sort_desc = sort_desc
        await self._session.flush()
        return layout
