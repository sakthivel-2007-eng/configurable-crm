"""Lead-field administration (M2).

The service behind `/settings/lead-fields` and its option editor. Everything
here runs through a `ScopedSession`, so a field or option belonging to another
workspace is invisible rather than forbidden.

Two rules worth stating because they shape every method below:

- **`key` is immutable.** Derived from the label at creation and never changed,
  because it is the JSONB key every stored value is filed under. Renaming a
  field changes its `label`; the data stays reachable.
- **Fields hide, options archive; neither deletes.** A deleted field would
  strand the values already written under its key, and a deleted option would
  turn every lead carrying it into a dangling reference.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.errors import api_error, conflict, not_found
from app.fields.registry import LEAD_FIELD_TYPES, lead_type_spec
from app.fields.values import slugify_key
from app.models.enums import LeadFieldType
from app.models.field import FieldOption, IndexedField, LeadField
from app.models.workspace import Workspace
from app.tenancy.session import ScopedSession
from app.workers.indexing import MAX_INDEXED_FIELDS, index_name

__all__ = ["FieldService"]

#: docs/03-configuration-model.md §1.6 — every new workspace gets these four,
#: renameable but never deletable. Deliberately generic: a name, two ways to
#: reach someone, and a spare number. Nothing about any industry.
BUILTIN_FIELDS: tuple[tuple[str, LeadFieldType], ...] = (
    ("Name", LeadFieldType.TEXT),
    ("Phone", LeadFieldType.PHONE),
    ("Email", LeadFieldType.EMAIL),
    ("Alternate Phone", LeadFieldType.PHONE),
)


class FieldService:
    """CRUD over a workspace's lead schema."""

    def __init__(self, session: ScopedSession) -> None:
        self._session = session

    # --- reads -------------------------------------------------------------

    async def list_fields(
        self,
        *,
        search: str | None = None,
        field_type: LeadFieldType | None = None,
        include_hidden: bool = False,
    ) -> Sequence[LeadField]:
        statement = (
            self._session.select(LeadField)
            .options(selectinload(LeadField.options))
            .order_by(LeadField.sort_order, LeadField.label)
        )
        if not include_hidden:
            statement = statement.where(LeadField.is_hidden.is_(False))
        if field_type is not None:
            statement = statement.where(LeadField.field_type == field_type)
        if search:
            statement = statement.where(LeadField.label.ilike(f"%{search}%"))

        rows = await self._session.execute(statement)
        fields: Sequence[LeadField] = rows.scalars().unique().all()
        return fields

    async def get_field(self, field_id: uuid.UUID) -> LeadField:
        rows = await self._session.execute(
            self._session.select(LeadField)
            .where(LeadField.id == field_id)
            .options(selectinload(LeadField.options))
            .limit(1)
        )
        field: LeadField | None = rows.scalar_one_or_none()
        if field is None:
            raise not_found("Field")
        return field

    async def options_by_field(self) -> dict[uuid.UUID, list[FieldOption]]:
        """Every option in the workspace, grouped by field.

        One query rather than one per field — the validator needs all of them
        for a single write, and a lead list needs them to decorate labels.
        """
        rows = await self._session.execute(
            self._session.select(FieldOption).order_by(FieldOption.sort_order)
        )
        grouped: dict[uuid.UUID, list[FieldOption]] = {}
        for option in rows.scalars().all():
            grouped.setdefault(option.field_id, []).append(option)
        return grouped

    # --- writes ------------------------------------------------------------

    async def create_field(
        self,
        *,
        label: str,
        field_type: LeadFieldType,
        description: str | None = None,
        is_required: bool = False,
        field_group: str | None = None,
        config: dict[str, Any] | None = None,
        show_in_import: bool = True,
        show_in_quick_add: bool = False,
        lock_after_create: bool = False,
        can_use_variable: bool = False,
        is_builtin: bool = False,
    ) -> LeadField:
        label = label.strip()
        if not 1 <= len(label) <= 40:
            raise api_error(422, "invalid_label", "Field name must be 1-40 characters")

        # `ScopedSession.select` takes a model, not a column — it scopes by
        # `model.workspace_id`, which a bare column does not have. Reading the
        # rows keeps the tenant filter intact; a workspace has tens of fields,
        # not thousands.
        existing = await self._session.execute(self._session.select(LeadField))
        key = slugify_key(label, taken={f.key for f in existing.scalars().all()})

        resolved_config = await self._validate_config(field_type, config or {})

        field = LeadField(
            key=key,
            label=label,
            field_type=field_type,
            description=description,
            is_required=is_required,
            field_group=field_group,
            config=resolved_config,
            show_in_import=show_in_import,
            show_in_quick_add=show_in_quick_add,
            lock_after_create=lock_after_create,
            can_use_variable=can_use_variable,
            is_builtin=is_builtin,
            sort_order=await self._next_sort_order(),
        )
        self._session.add(field)
        await self._session.flush()
        return field

    async def update_field(
        self,
        field_id: uuid.UUID,
        *,
        label: str | None = None,
        description: str | None = None,
        is_required: bool | None = None,
        field_group: str | None = None,
        config: dict[str, Any] | None = None,
        show_in_import: bool | None = None,
        show_in_quick_add: bool | None = None,
        lock_after_create: bool | None = None,
        can_use_variable: bool | None = None,
        sort_order: int | None = None,
    ) -> LeadField:
        """Update a field. `key` and `field_type` are deliberately absent.

        Changing the type would invalidate every value already stored under the
        key; changing the key would orphan them. Both are create-time
        decisions.
        """
        field = await self.get_field(field_id)

        if label is not None:
            label = label.strip()
            if not 1 <= len(label) <= 40:
                raise api_error(422, "invalid_label", "Field name must be 1-40 characters")
            field.label = label
        if description is not None:
            field.description = description
        if is_required is not None:
            field.is_required = is_required
        if field_group is not None:
            field.field_group = field_group or None
        if config is not None:
            field.config = await self._validate_config(field.field_type, config)
        if show_in_import is not None:
            field.show_in_import = show_in_import
        if show_in_quick_add is not None:
            field.show_in_quick_add = show_in_quick_add
        if lock_after_create is not None:
            field.lock_after_create = lock_after_create
        if can_use_variable is not None:
            field.can_use_variable = can_use_variable
        if sort_order is not None:
            field.sort_order = sort_order

        await self._session.flush()
        return field

    async def set_hidden(self, field_id: uuid.UUID, *, hidden: bool) -> LeadField:
        """Hide or unhide. Built-in fields cannot be hidden.

        The identity field cannot be hidden either — the workspace would lose
        the ability to dedupe.
        """
        field = await self.get_field(field_id)
        if hidden and field.is_builtin:
            raise api_error(
                409, "builtin_field", f"{field.label} is a built-in field and cannot be hidden"
            )
        if hidden:
            workspace = await self._workspace()
            if workspace.identity_field_id == field.id:
                raise conflict(
                    "identity_field_in_use",
                    "This field is the workspace's lead identifier. "
                    "Choose a different identifier before hiding it.",
                )
        field.is_hidden = hidden
        await self._session.flush()
        return field

    # --- options -----------------------------------------------------------

    async def list_options(self, field_id: uuid.UUID) -> Sequence[FieldOption]:
        await self.get_field(field_id)  # 404s for a foreign field
        rows = await self._session.execute(
            self._session.select(FieldOption)
            .where(FieldOption.field_id == field_id)
            .order_by(FieldOption.sort_order)
        )
        options: Sequence[FieldOption] = rows.scalars().all()
        return options

    async def add_option(
        self,
        field_id: uuid.UUID,
        *,
        label: str,
        color: str | None = None,
        parent_option_id: uuid.UUID | None = None,
        code: str | None = None,
    ) -> FieldOption:
        field = await self._require_option_field(field_id)
        label = label.strip()
        if not 1 <= len(label) <= 70:
            raise api_error(422, "invalid_label", "Option label must be 1-70 characters")

        if parent_option_id is not None:
            if field.field_type is not LeadFieldType.DEPENDENT_DROPDOWN:
                raise api_error(
                    422,
                    "not_a_cascade",
                    "Only a dependent dropdown's options can have a parent",
                )
            # Scoped get: a parent from another workspace reads as absent.
            parent = await self._session.get(FieldOption, parent_option_id)
            if parent is None or parent.field_id != field_id:
                raise api_error(422, "unknown_parent_option", "Parent option not found")

        existing_codes = {o.code for o in await self.list_options(field_id)}
        option = FieldOption(
            field_id=field_id,
            parent_option_id=parent_option_id,
            code=code or slugify_key(label, taken=existing_codes),
            label=label,
            color=color,
            sort_order=len(existing_codes),
        )
        self._session.add(option)
        await self._session.flush()
        return option

    async def add_options_bulk(
        self, field_id: uuid.UUID, *, labels: Sequence[str]
    ) -> list[FieldOption]:
        """ "Add multiple" — one option per pasted line.

        Blank lines and duplicates of existing labels are skipped rather than
        erroring: pasting a column out of a spreadsheet reliably includes both.
        """
        await self._require_option_field(field_id)
        current = list(await self.list_options(field_id))
        taken = {o.code for o in current}
        seen_labels = {o.label.casefold() for o in current}

        created: list[FieldOption] = []
        for offset, raw in enumerate(labels):
            label = raw.strip()
            if not label or label.casefold() in seen_labels:
                continue
            if len(label) > 70:
                raise api_error(
                    422, "invalid_label", f"Option label {label!r} is longer than 70 characters"
                )
            code = slugify_key(label, taken=taken)
            taken.add(code)
            seen_labels.add(label.casefold())
            option = FieldOption(
                field_id=field_id,
                code=code,
                label=label,
                sort_order=len(current) + offset,
            )
            self._session.add(option)
            created.append(option)

        await self._session.flush()
        return created

    async def copy_options_from(
        self, field_id: uuid.UUID, *, source_field_id: uuid.UUID
    ) -> list[FieldOption]:
        """ "Copy options" — clone another field's option set, tree and all.

        Both fields are read through the scoped session, so copying from
        another workspace's field is a 404, not a cross-tenant read.
        """
        await self._require_option_field(field_id)
        await self._require_option_field(source_field_id)

        source = list(await self.list_options(source_field_id))
        taken = {o.code for o in await self.list_options(field_id)}

        # Two passes so parents exist before children reference them.
        id_map: dict[uuid.UUID, FieldOption] = {}
        created: list[FieldOption] = []
        for option in sorted(source, key=lambda o: (o.parent_option_id is not None, o.sort_order)):
            if option.code in taken:
                continue
            clone = FieldOption(
                field_id=field_id,
                code=option.code,
                label=option.label,
                color=option.color,
                sort_order=option.sort_order,
                is_archived=option.is_archived,
            )
            if option.parent_option_id and option.parent_option_id in id_map:
                await self._session.flush()
                clone.parent_option_id = id_map[option.parent_option_id].id
            self._session.add(clone)
            await self._session.flush()
            id_map[option.id] = clone
            created.append(clone)

        return created

    async def update_option(
        self,
        option_id: uuid.UUID,
        *,
        label: str | None = None,
        color: str | None = None,
        sort_order: int | None = None,
    ) -> FieldOption:
        option = await self._session.get(FieldOption, option_id)
        if option is None:
            raise not_found("Option")
        if label is not None:
            label = label.strip()
            if not 1 <= len(label) <= 70:
                raise api_error(422, "invalid_label", "Option label must be 1-70 characters")
            option.label = label
        if color is not None:
            option.color = color or None
        if sort_order is not None:
            option.sort_order = sort_order
        await self._session.flush()
        return option

    async def archive_option(self, option_id: uuid.UUID) -> FieldOption:
        """Archive, never delete.

        §2.3: "Leads that referenced a deleted status keep it — deletion
        removes it from the picker, not from history." The same holds for
        options.
        """
        option = await self._session.get(FieldOption, option_id)
        if option is None:
            raise not_found("Option")
        option.is_archived = True
        await self._session.flush()
        return option

    async def reorder_options(
        self, field_id: uuid.UUID, *, ordered_ids: Sequence[uuid.UUID]
    ) -> Sequence[FieldOption]:
        options = {o.id: o for o in await self.list_options(field_id)}
        unknown = [i for i in ordered_ids if i not in options]
        if unknown:
            raise api_error(422, "unknown_option", "One or more options are not on this field")
        for position, option_id in enumerate(ordered_ids):
            options[option_id].sort_order = position
        await self._session.flush()
        return await self.list_options(field_id)

    # --- workspace-level field settings ------------------------------------

    async def set_identity_field(self, field_id: uuid.UUID) -> Workspace:
        """Designate the field that uniquely identifies a lead.

        §1.1: "This is configurable per workspace, not a constant. A B2B
        customer might key on Email; a dealership on registration number."

        Changing it triggers an `identity_value` backfill in M5 — recorded here
        as the setting change; the backfill job belongs to the milestone that
        owns the column.
        """
        field = await self.get_field(field_id)
        if field.is_hidden:
            raise api_error(422, "hidden_field", "A hidden field cannot be the lead identifier")
        if field.field_type in (LeadFieldType.TAGS, LeadFieldType.CHECKBOX):
            raise api_error(
                422,
                "unsuitable_identity_field",
                f"A {field.field_type.value} field cannot uniquely identify a lead",
            )
        workspace = await self._workspace()
        workspace.identity_field_id = field.id
        await self._session.flush()
        return workspace

    async def set_primary_fields(
        self, *, h1_field_id: uuid.UUID, h2_field_id: uuid.UUID | None
    ) -> Workspace:
        """H1/H2 — the headline fields on cards, list rows and the detail header."""
        await self.get_field(h1_field_id)
        if h2_field_id is not None:
            await self.get_field(h2_field_id)
        workspace = await self._workspace()
        workspace.primary_field_1_id = h1_field_id
        workspace.primary_field_2_id = h2_field_id
        await self._session.flush()
        return workspace

    # --- indexed fields ----------------------------------------------------

    async def list_indexed(self) -> Sequence[IndexedField]:
        rows = await self._session.execute(
            self._session.select(IndexedField).options(selectinload(IndexedField.field))
        )
        entries: Sequence[IndexedField] = rows.scalars().unique().all()
        return entries

    async def declare_indexed(self, field_id: uuid.UUID) -> IndexedField:
        """Mark a field indexed, returning immediately with PENDING.

        The index itself is built by the worker — `CREATE INDEX CONCURRENTLY`
        cannot run inside this request's transaction.
        """
        field = await self.get_field(field_id)

        current = await self.list_indexed()
        if any(i.field_id == field_id for i in current):
            raise conflict("already_indexed", f"{field.label} is already indexed")
        if len(current) >= MAX_INDEXED_FIELDS:
            raise conflict(
                "indexed_field_limit",
                f"A workspace may index at most {MAX_INDEXED_FIELDS} fields",
                indexed_limit=MAX_INDEXED_FIELDS,
                indexed_used=len(current),
            )

        entry = IndexedField(
            field_id=field_id,
            index_name=index_name(self._session.workspace_id, field_id),
            status="PENDING",
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def undeclare_indexed(self, field_id: uuid.UUID) -> str:
        """Remove the declaration, returning the index name for the worker.

        This is one of the few genuine row deletions in the product, and it is
        justified: an `indexed_fields` row is an *operational declaration*, not
        customer data. Archiving instead would leave the
        `(workspace_id, field_id)` unique constraint occupied, so a workspace
        could never re-index a field it had once un-indexed.

        The `DELETE` names `workspace_id` explicitly. The scoped session's
        loader criteria applies to `SELECT` only, so a bare `DELETE ... WHERE
        id = :id` would not be tenant-filtered — the row was already fetched
        through the scope, but defence in depth is cheaper than the incident.
        """
        rows = await self._session.execute(
            self._session.select(IndexedField).where(IndexedField.field_id == field_id).limit(1)
        )
        entry: IndexedField | None = rows.scalar_one_or_none()
        if entry is None:
            raise not_found("Indexed field")

        declared_name: str = entry.index_name
        await self._session.execute(
            delete(IndexedField).where(
                IndexedField.id == entry.id,
                IndexedField.workspace_id == self._session.workspace_id,
            )
        )
        return declared_name

    # --- internals ---------------------------------------------------------

    async def _workspace(self) -> Workspace:
        """The workspace row, for the settings that live on it.

        Read through the underlying session rather than `ScopedSession.select`,
        which by design accepts only `TenantModel` subclasses — `Workspace` is
        the tenant, not tenant data. The id still comes from the scope, so this
        cannot address another workspace.
        """
        result = await self._session.execute(
            select(Workspace).where(Workspace.id == self._session.workspace_id).limit(1)
        )
        workspace: Workspace | None = result.scalar_one_or_none()
        if workspace is None:  # pragma: no cover — the scope proved it exists
            raise not_found("Workspace")
        return workspace

    async def _next_sort_order(self) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(LeadField.sort_order), -1) + 1).where(
                LeadField.workspace_id == self._session.workspace_id
            )
        )
        return int(result.scalar_one())

    async def _require_option_field(self, field_id: uuid.UUID) -> LeadField:
        field = await self.get_field(field_id)
        if not lead_type_spec(field.field_type).uses_options:
            raise api_error(
                422,
                "type_has_no_options",
                f"A {field.field_type.value} field does not have options",
            )
        return field

    async def _validate_config(
        self, field_type: LeadFieldType, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Keep only keys the type's `config_schema` declares.

        Silently dropping unknown keys rather than erroring, because the
        settings UI renders the schema generically and may post back more than
        a given type uses.
        """
        schema = LEAD_FIELD_TYPES[field_type].config_schema
        cleaned = {k: v for k, v in config.items() if k in schema}

        parent_id = cleaned.get("parent_field_id")
        if parent_id:
            try:
                parent_uuid = uuid.UUID(str(parent_id))
            except (TypeError, ValueError) as exc:
                raise api_error(
                    422, "invalid_parent_field", "parent_field_id must be a field id"
                ) from exc
            await self.get_field(parent_uuid)  # 404s across workspaces
            cleaned["parent_field_id"] = str(parent_uuid)
        return cleaned
