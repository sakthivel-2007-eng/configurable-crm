"""Permission template administration (M4).

The field matrix (§6.4) and "Set up your lead view" (§6.3).

The matrix is stored as one row per granted (template, field, grant). Writes are
a **full replace** for the fields named, because the editor's mental model is a
grid of checkboxes: what you see is what is stored. A partial update would make
"unticked" and "not mentioned" indistinguishable.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.errors import api_error, conflict, not_found
from app.models.enums import PermissionGrant
from app.models.field import LeadField
from app.models.permission import TemplateFieldGrant, TemplateLeadView
from app.models.workspace import Membership, PermissionTemplate
from app.permissions.capabilities import Capabilities
from app.tenancy.session import ScopedSession

__all__ = ["PermissionTemplateService"]

#: Grants a newly created template gets on every existing field. View only —
#: the observed default for Export is `(0) None`, and starting a new template
#: with edit rights on everything defeats the point of having a matrix.
DEFAULT_NEW_TEMPLATE_GRANTS: tuple[PermissionGrant, ...] = (PermissionGrant.VIEW,)


class PermissionTemplateService:
    """CRUD over permission templates, their field matrix and their lead view."""

    def __init__(self, session: ScopedSession) -> None:
        self._session = session

    # --- templates ---------------------------------------------------------

    async def list_templates(self) -> Sequence[PermissionTemplate]:
        rows = await self._session.execute(
            self._session.select(PermissionTemplate).order_by(PermissionTemplate.name)
        )
        templates: Sequence[PermissionTemplate] = rows.scalars().all()
        return templates

    async def get_template(self, template_id: uuid.UUID) -> PermissionTemplate:
        template = await self._session.get(PermissionTemplate, template_id)
        if template is None:
            raise not_found("Permission template")
        return template

    async def create_template(
        self, *, name: str, capabilities: Mapping[str, Any] | None = None
    ) -> PermissionTemplate:
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise api_error(422, "invalid_label", "A template name must be 1-80 characters")

        existing = await self.list_templates()
        if any(t.name.casefold() == name.casefold() for t in existing):
            raise conflict("duplicate_template", f"A template called {name!r} already exists")

        template = PermissionTemplate(
            name=name,
            is_system=False,
            is_readonly=False,
            # Validated on the way in: a blob nobody validates becomes a blob
            # nobody understands (§3.5).
            capabilities=Capabilities.model_validate(capabilities or {}).model_dump(),
        )
        self._session.add(template)
        await self._session.flush()

        # Seed the matrix with View on every current field, so a new template
        # is usable immediately without being permissive.
        fields = await self._all_fields()
        self._session.add_all(
            [
                TemplateFieldGrant(
                    workspace_id=self._session.workspace_id,
                    template_id=template.id,
                    field_id=field.id,
                    grant=grant,
                )
                for field in fields
                for grant in DEFAULT_NEW_TEMPLATE_GRANTS
            ]
        )
        await self._session.flush()
        return template

    async def update_template(
        self,
        template_id: uuid.UUID,
        *,
        name: str | None = None,
        capabilities: Mapping[str, Any] | None = None,
        updated_by_id: uuid.UUID | None = None,
    ) -> PermissionTemplate:
        template = await self._require_editable(template_id)
        if name is not None:
            template.name = name.strip()
        if capabilities is not None:
            template.capabilities = Capabilities.model_validate(capabilities).model_dump()
        if updated_by_id is not None:
            template.updated_by_id = updated_by_id
        await self._session.flush()
        return template

    async def delete_template(self, template_id: uuid.UUID) -> None:
        """Refuse while anyone holds it (`409`, per the API contract).

        Deleting an assigned template would leave a membership pointing at
        nothing, and the scoping dependency would then fail to resolve a
        template for a user who could previously log in.
        """
        template = await self._require_editable(template_id)
        holders = await self._session.execute(
            select(Membership.id).where(
                Membership.template_id == template.id,
                Membership.workspace_id == self._session.workspace_id,
            )
        )
        assigned = len(list(holders.scalars().all()))
        if assigned:
            raise conflict(
                "template_assigned",
                f"{template.name} is assigned to {assigned} member(s). "
                f"Move them to another template first.",
                assigned=assigned,
            )
        await self._session.execute(
            delete(PermissionTemplate).where(
                PermissionTemplate.id == template.id,
                PermissionTemplate.workspace_id == self._session.workspace_id,
            )
        )

    # --- the field matrix --------------------------------------------------

    async def field_matrix(self, template_id: uuid.UUID) -> dict[str, Any]:
        """The matrix plus the per-column rollups the editor renders.

        Rollups (`Full` / `Partial` / `None`) and live counts are computed here
        rather than in the frontend so the badge cannot disagree with the data.
        """
        await self.get_template(template_id)
        fields = await self._all_fields()
        granted = await self._grants_for(template_id)

        rows = [
            {
                "field_id": str(field.id),
                "key": field.key,
                "label": field.label,
                "field_type": field.field_type.value,
                "is_hidden": field.is_hidden,
                **{grant.value.lower(): field.id in granted[grant] for grant in PermissionGrant},
            }
            for field in fields
        ]

        total = len(fields)
        columns = {}
        for grant in PermissionGrant:
            count = len(granted[grant] & {f.id for f in fields})
            columns[grant.value.lower()] = {
                "count": count,
                "total": total,
                "rollup": "Full"
                if count == total and total
                else ("None" if not count else "Partial"),
            }

        return {"fields": rows, "columns": columns}

    async def replace_field_grants(
        self, template_id: uuid.UUID, *, grants: Sequence[Mapping[str, Any]]
    ) -> None:
        """Full replace for the fields named in the payload.

        Only the named fields are touched: the editor may page through a long
        field list, and a full-table replace would wipe grants for fields not
        on screen.
        """
        await self._require_editable(template_id)
        by_id = {f.id: f for f in await self._all_fields()}

        touched: list[uuid.UUID] = []
        wanted: list[TemplateFieldGrant] = []
        for entry in grants:
            try:
                field_id = uuid.UUID(str(entry["field_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise api_error(422, "invalid_grant", "Each grant needs a field_id") from exc
            if field_id not in by_id:
                # A field from another workspace, or one since removed.
                raise not_found("Field")
            touched.append(field_id)
            for grant in PermissionGrant:
                if entry.get(grant.value.lower(), False):
                    wanted.append(
                        TemplateFieldGrant(
                            workspace_id=self._session.workspace_id,
                            template_id=template_id,
                            field_id=field_id,
                            grant=grant,
                        )
                    )

        if touched:
            await self._session.execute(
                delete(TemplateFieldGrant).where(
                    TemplateFieldGrant.template_id == template_id,
                    TemplateFieldGrant.workspace_id == self._session.workspace_id,
                    TemplateFieldGrant.field_id.in_(touched),
                )
            )
        for row in wanted:
            self._session.add(row)
        await self._session.flush()

    async def bulk_set_grant(
        self,
        template_id: uuid.UUID,
        *,
        grant: PermissionGrant,
        value: bool,
        field_ids: Sequence[uuid.UUID] | None = None,
    ) -> None:
        """The column select-all: one grant across many fields at once."""
        await self._require_editable(template_id)
        by_id = {f.id: f for f in await self._all_fields()}
        targets = list(by_id) if field_ids is None else [uuid.UUID(str(f)) for f in field_ids]

        unknown = [f for f in targets if f not in by_id]
        if unknown:
            raise not_found("Field")

        await self._session.execute(
            delete(TemplateFieldGrant).where(
                TemplateFieldGrant.template_id == template_id,
                TemplateFieldGrant.workspace_id == self._session.workspace_id,
                TemplateFieldGrant.grant == grant,
                TemplateFieldGrant.field_id.in_(targets),
            )
        )
        if value:
            for field_id in targets:
                self._session.add(
                    TemplateFieldGrant(
                        workspace_id=self._session.workspace_id,
                        template_id=template_id,
                        field_id=field_id,
                        grant=grant,
                    )
                )
        await self._session.flush()

    # --- lead view ---------------------------------------------------------

    async def get_lead_view(self, template_id: uuid.UUID) -> list[dict[str, Any]]:
        """The per-template lead-detail layout (§6.3).

        An empty layout means "no custom layout" — the client falls back to the
        field list in `sort_order`, which is what a workspace that never opened
        this screen should see.
        """
        await self.get_template(template_id)
        rows = await self._session.execute(
            self._session.select(TemplateLeadView)
            .where(TemplateLeadView.template_id == template_id)
            .limit(1)
        )
        view: TemplateLeadView | None = rows.scalar_one_or_none()
        return list(view.layout) if view else []

    async def set_lead_view(
        self, template_id: uuid.UUID, *, layout: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace the layout, validating every field id in it."""
        await self._require_editable(template_id)
        by_id = {f.id: f for f in await self._all_fields()}

        cleaned: list[dict[str, Any]] = []
        for group in layout:
            field_ids: list[str] = []
            for raw in group.get("field_ids", []):
                try:
                    field_id = uuid.UUID(str(raw))
                except (TypeError, ValueError) as exc:
                    raise api_error(422, "invalid_layout", f"{raw!r} is not a field id") from exc
                if field_id not in by_id:
                    raise not_found("Field")
                field_ids.append(str(field_id))
            cleaned.append(
                {
                    "label": str(group.get("label") or "").strip() or "Details",
                    "collapsed": bool(group.get("collapsed", False)),
                    "field_ids": field_ids,
                }
            )

        rows = await self._session.execute(
            self._session.select(TemplateLeadView)
            .where(TemplateLeadView.template_id == template_id)
            .limit(1)
        )
        existing: TemplateLeadView | None = rows.scalar_one_or_none()
        if existing is None:
            self._session.add(TemplateLeadView(template_id=template_id, layout=cleaned))
        else:
            existing.layout = cleaned
        await self._session.flush()
        return cleaned

    # --- assignees ---------------------------------------------------------

    async def list_assignees(self, template_id: uuid.UUID) -> Sequence[Membership]:
        await self.get_template(template_id)
        rows = await self._session.execute(
            self._session.select(Membership)
            .where(Membership.template_id == template_id)
            .options(selectinload(Membership.user))
        )
        members: Sequence[Membership] = rows.scalars().unique().all()
        return members

    # --- internals ---------------------------------------------------------

    async def _all_fields(self) -> Sequence[LeadField]:
        rows = await self._session.execute(
            self._session.select(LeadField).order_by(LeadField.sort_order, LeadField.label)
        )
        fields: Sequence[LeadField] = rows.scalars().all()
        return fields

    async def _grants_for(self, template_id: uuid.UUID) -> dict[PermissionGrant, set[uuid.UUID]]:
        rows = await self._session.execute(
            select(TemplateFieldGrant).where(
                TemplateFieldGrant.template_id == template_id,
                TemplateFieldGrant.workspace_id == self._session.workspace_id,
            )
        )
        buckets: dict[PermissionGrant, set[uuid.UUID]] = {g: set() for g in PermissionGrant}
        for row in rows.scalars().all():
            buckets[row.grant].add(row.field_id)
        return buckets

    async def _require_editable(self, template_id: uuid.UUID) -> PermissionTemplate:
        """Root is view-only (§6.1).

        It is the escape hatch that has to keep working when an admin
        misconfigures everything else, so it cannot be edited into uselessness.
        """
        template = await self.get_template(template_id)
        if template.is_readonly:
            raise api_error(
                403,
                "template_readonly",
                f"{template.name} is read-only. It is the fallback that must keep "
                f"working when other templates are misconfigured.",
            )
        return template
