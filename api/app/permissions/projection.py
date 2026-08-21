"""`FieldProjectionService` and `FieldWriteFilter` — the two chokepoints.

CLAUDE.md architecture rules 3 and 4, and the single most important thing in
`docs/03-configuration-model.md`:

> Enforce this in **one** place — a field-projection service every read and
> write passes through. Scattering it across endpoints guarantees a leak.

**Every** lead read goes through `FieldProjectionService`: list, detail, export,
webhook payloads, reports, message-template rendering. There is no "internal"
caller that skips it — an internal caller is exactly how the first leak happens.

**Every** lead write goes through `FieldWriteFilter`. A field the caller cannot
edit is **rejected**, never silently dropped:

> Fields without `Edit` are rejected with an error, never silently dropped.

Silently dropping is worse than erroring because the user believes they saved
something they did not.

Grants are rows in `template_field_grants`: presence means granted, absence
means denied. There is no explicit deny, and the observed default for Export is
`(0) None` — exporting is off unless someone turns it on.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select

from app.errors import forbidden
from app.models.enums import PermissionGrant
from app.models.permission import TemplateFieldGrant
from app.tenancy.session import ScopedSession

__all__ = ["FieldGrants", "FieldProjectionService", "FieldWriteFilter", "load_grants"]


@dataclasses.dataclass(frozen=True, slots=True)
class FieldGrants:
    """One template's resolved field matrix.

    Four independent sets of field *keys*. Keys rather than ids because every
    consumer — the JSONB blob, the filter DSL, an import mapping — speaks keys.

    Resolved once per request and cached on the scope (PROMPTS.md M4: "Cache
    resolved permissions per request"), because a 100-lead list page would
    otherwise re-derive the same matrix 100 times.
    """

    view: frozenset[str]
    edit: frozenset[str]
    import_: frozenset[str]
    export: frozenset[str]
    #: True when the template holds admin access on leads, in which case the
    #: matrix is bypassed entirely — an admin sees every field, including ones
    #: created after their template was last edited.
    is_admin: bool = False

    def can_view(self, key: str) -> bool:
        return self.is_admin or key in self.view

    def can_edit(self, key: str) -> bool:
        return self.is_admin or key in self.edit

    def can_import(self, key: str) -> bool:
        return self.is_admin or key in self.import_

    def can_export(self, key: str) -> bool:
        return self.is_admin or key in self.export

    @property
    def exports_anything(self) -> bool:
        """Whether an export is permitted at all.

        The observed default is `Export (0) None` — a deliberate
        data-exfiltration control. An export job for a template granting no
        export fields is refused outright rather than producing an empty file.
        """
        return self.is_admin or bool(self.export)


async def load_grants(
    session: ScopedSession,
    *,
    template_id: uuid.UUID,
    is_admin: bool,
    all_field_keys: Mapping[uuid.UUID, str],
) -> FieldGrants:
    """Read one template's matrix into a `FieldGrants`.

    `all_field_keys` maps field id -> key and is supplied by the caller, which
    has usually just loaded the workspace's fields anyway. Passing it in keeps
    this to a single query.
    """
    if is_admin:
        every = frozenset(all_field_keys.values())
        return FieldGrants(view=every, edit=every, import_=every, export=every, is_admin=True)

    rows = await session.execute(
        select(TemplateFieldGrant).where(
            TemplateFieldGrant.template_id == template_id,
            TemplateFieldGrant.workspace_id == session.workspace_id,
        )
    )

    buckets: dict[PermissionGrant, set[str]] = {g: set() for g in PermissionGrant}
    for row in rows.scalars().all():
        key = all_field_keys.get(row.field_id)
        if key is not None:  # a grant for a field since removed is inert
            buckets[row.grant].add(key)

    return FieldGrants(
        view=frozenset(buckets[PermissionGrant.VIEW]),
        edit=frozenset(buckets[PermissionGrant.EDIT]),
        import_=frozenset(buckets[PermissionGrant.IMPORT]),
        export=frozenset(buckets[PermissionGrant.EXPORT]),
        is_admin=False,
    )


class FieldProjectionService:
    """Strips fields the caller may not View. Every read passes through here.

    Deliberately a tiny class. Its value is not its logic — it is that there is
    exactly one of it, so "did this endpoint remember to filter?" has one
    answer for the whole product.
    """

    def __init__(self, grants: FieldGrants) -> None:
        self._grants = grants

    @property
    def grants(self) -> FieldGrants:
        return self._grants

    def project_values(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """The View-permitted subset of a stored `values` blob.

        Non-View keys are *absent*, not null. A null would tell the caller the
        field exists and is empty; absence tells them nothing at all.
        """
        return {k: v for k, v in values.items() if self._grants.can_view(k)}

    def project_export(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """The Export-permitted subset.

        A separate grant from View: a caller may legitimately read a phone
        number on screen and be barred from downloading ten thousand of them.
        """
        return {k: v for k, v in values.items() if self._grants.can_export(k)}

    def visible_field_keys(self, all_keys: Sequence[str]) -> list[str]:
        """Which of a workspace's fields this caller may see.

        Used for column pickers, import mapping and the lead-view layout, so
        the UI never offers a field the API would then strip.
        """
        return [k for k in all_keys if self._grants.can_view(k)]

    def assert_can_export(self) -> None:
        if not self._grants.exports_anything:
            raise forbidden(
                "export_not_permitted",
                "This permission template does not allow exporting any field",
            )

    def filterable(self, key: str) -> bool:
        """Whether a field may be filtered or sorted on.

        Bound to View: filtering on a hidden field is a read. `stage = X AND
        salary > 100000` returning a count is an oracle over a field the caller
        cannot see, so the filter compiler in M6 asks this first.
        """
        return self._grants.can_view(key)


class FieldWriteFilter:
    """Rejects writes to fields the caller may not Edit.

    Rejects — never drops. The user who typed a value into a field they cannot
    edit must be told, not left believing it saved.
    """

    def __init__(self, grants: FieldGrants) -> None:
        self._grants = grants

    def check(self, values: Mapping[str, Any], *, known_keys: frozenset[str]) -> None:
        """Raise `403 field_not_editable` naming every offending field.

        All of them at once: a form with three forbidden fields should say so
        once rather than three times.

        `known_keys` are the workspace's real field keys. A key that matches no
        field is not a permission problem — the intake API is required to accept
        unknown keys with a warning — so it passes here and is handled by the
        validator.
        """
        refused = sorted(
            key for key in values if key in known_keys and not self._grants.can_edit(key)
        )
        if refused:
            raise forbidden(
                "field_not_editable",
                (f"This permission template does not allow editing: {', '.join(refused)}"),
                fields=refused,
            )

    def can_import(self, key: str) -> bool:
        """The Import grant as a question rather than an assertion.

        `check_import` refuses a mapping the caller submitted; this decides
        what to *offer* them in the first place, so the mapping UI never shows
        a field the API would then reject.
        """
        return self._grants.can_import(key)

    def check_import(self, keys: Sequence[str], *, known_keys: frozenset[str]) -> None:
        """The Import grant, which is independent of Edit.

        An import mapping offering a field the caller cannot import is rejected
        with `400` per the contract's test matrix, so the mapping UI and the API
        agree on what may be mapped.
        """
        refused = sorted(
            key for key in keys if key in known_keys and not self._grants.can_import(key)
        )
        if refused:
            raise forbidden(
                "field_not_importable",
                f"This permission template does not allow importing: {', '.join(refused)}",
                fields=refused,
            )
