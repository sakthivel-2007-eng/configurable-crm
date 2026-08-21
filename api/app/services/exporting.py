"""Export, duplicates and merge (M7).

**Export honours the Export grant, which is not the View grant.** A caller may
legitimately read a phone number on screen and be barred from downloading ten
thousand of them; `Export (0) None` is the observed default and a deliberate
exfiltration control. A template granting no export field is refused outright
rather than handed an empty file, because an empty file looks like a bug and
invites a retry.

**Duplicates are not "same identity value".** `leads_identity_uq` makes that
impossible: two live leads in one workspace cannot share an identity. Taking the
contract's phrase literally would ship a screen that is always empty. What
people actually mean by a duplicate is the same person entered twice — their
number in the identity field on one record and in an alternate-contact field on
the other — so the report groups by *any* phone or email value the workspace
holds, which is the case that really occurs.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, update

from app.errors import api_error, conflict, not_found
from app.fields.search import SEARCH_CONFIG, search_text_for
from app.models.enums import ChangesetSource, ImportJobKind, ImportJobStatus, LeadFieldType
from app.models.field import LeadField
from app.models.lead import Action, Lead
from app.models.work import ImportJob, LeadLabel, Task
from app.models.workspace import Workspace
from app.permissions.projection import FieldProjectionService
from app.services.actions import ActionWriter, FieldDelta
from app.tenancy.session import ScopedSession

__all__ = ["DuplicateGroup", "ExportService"]

#: Rows written into one export. Above this the answer is a filtered export,
#: not a bigger file — and a 200MB CSV helps nobody.
MAX_EXPORT_ROWS = 50_000

#: Built-in columns every export carries, before the workspace's own fields.
_BUILTIN_HEADERS = ("Identifier", "Stage", "Assignee", "Rating", "Score", "Created")

#: Field types whose values identify a person, and so can reveal a duplicate.
_CONTACT_TYPES = frozenset({LeadFieldType.PHONE, LeadFieldType.EMAIL})


@dataclasses.dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """Leads that appear to be the same person."""

    value: str
    lead_ids: tuple[uuid.UUID, ...]
    identity_values: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "lead_ids": [str(i) for i in self.lead_ids],
            "identity_values": list(self.identity_values),
        }


class ExportService:
    """Export a filtered set, find likely duplicates, and merge them."""

    def __init__(
        self,
        session: ScopedSession,
        *,
        workspace: Workspace,
        projection: FieldProjectionService,
        actor_id: uuid.UUID | None,
        storage: Any = None,
        bucket: str | None = None,
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._projection = projection
        self._actor_id = actor_id
        self._storage = storage
        self._bucket = bucket

    async def _fields(self) -> Sequence[LeadField]:
        rows = await self._session.execute(
            self._session.select(LeadField).order_by(LeadField.sort_order)
        )
        fields: Sequence[LeadField] = rows.scalars().all()
        return fields

    # --- export -------------------------------------------------------------

    async def export(self, *, clause: Any = None, filename: str = "leads.csv") -> ImportJob:
        """Write the caller's Export-granted columns to a file.

        Refused up front if the template exports nothing — see the module
        docstring. `assert_can_export` is the same check the projection has
        carried since M4; this is the first endpoint to call it.
        """
        self._projection.assert_can_export()

        fields = await self._fields()
        exportable = [f for f in fields if self._projection.grants.can_export(f.key)]

        statement = self._session.select(Lead).where(Lead.deleted_at.is_(None))
        if clause is not None:
            statement = statement.where(clause)

        total = await self._session.execute(func.count().select().select_from(statement.subquery()))
        row_count = int(total.scalar_one())
        if row_count > MAX_EXPORT_ROWS:
            raise api_error(
                422,
                "export_too_large",
                f"An export covers at most {MAX_EXPORT_ROWS:,} leads. Filter first.",
                limit=MAX_EXPORT_ROWS,
                matched=row_count,
            )

        rows = await self._session.execute(statement.order_by(Lead.created_at))
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([*_BUILTIN_HEADERS, *(f.label for f in exportable)])

        for lead in rows.scalars():
            # Through the projection, not the raw blob. There is no "internal"
            # reader that skips it, and an export is the read most worth
            # getting right.
            permitted = self._projection.project_export(lead.values or {})
            writer.writerow(
                [
                    lead.identity_value,
                    str(lead.stage_id or ""),
                    str(lead.assignee_id or ""),
                    lead.rating if lead.rating is not None else "",
                    lead.score,
                    lead.created_at.isoformat(),
                    *(_flatten(permitted.get(f.key)) for f in exportable),
                ]
            )

        content = buffer.getvalue().encode()
        job = ImportJob(
            kind=ImportJobKind.EXPORT,
            status=ImportJobStatus.COMPLETED,
            filename=filename,
            row_count=row_count,
            result={
                "columns": [*_BUILTIN_HEADERS, *(f.label for f in exportable)],
                "bytes": len(content),
            },
            created_by_id=self._actor_id,
        )
        self._session.add(job)
        await self._session.flush()

        job.storage_key = f"exports/{self._workspace.id}/{job.id}/{filename}"
        if self._storage is not None and self._bucket:
            self._storage.put_object(Bucket=self._bucket, Key=job.storage_key, Body=content)
        await self._session.flush()
        return job

    async def download(self, job_id: uuid.UUID) -> tuple[str, bytes]:
        job = await self._session.get(ImportJob, job_id)
        if job is None or job.kind is not ImportJobKind.EXPORT:
            raise not_found("Export")
        if self._storage is None or not self._bucket or not job.storage_key:
            raise conflict("export_unavailable", "That export is no longer available")
        response = self._storage.get_object(Bucket=self._bucket, Key=job.storage_key)
        return job.filename, response["Body"].read()

    # --- duplicates ---------------------------------------------------------

    async def duplicates(self, *, limit: int = 50) -> list[DuplicateGroup]:
        """Leads that look like the same person.

        Grouped on every phone and email value the workspace holds, not just
        the identity field — the identity field is unique by construction, so
        grouping on it alone would always return nothing.
        """
        fields = await self._fields()
        contact_keys = [
            f.key
            for f in fields
            if f.field_type in _CONTACT_TYPES and self._projection.filterable(f.key)
        ]

        rows = await self._session.execute(
            self._session.select(Lead).where(Lead.deleted_at.is_(None))
        )
        by_value: dict[str, list[Lead]] = {}
        for lead in rows.scalars():
            seen: set[str] = set()
            values = lead.values or {}
            for key in contact_keys:
                raw = values.get(key)
                if isinstance(raw, str) and raw.strip():
                    seen.add(raw.strip().lower())
            # The identity value counts too: one record may carry the number as
            # its identifier while another carries it as an alternate contact.
            seen.add(lead.identity_value.strip().lower())
            for value in seen:
                by_value.setdefault(value, []).append(lead)

        groups = [
            DuplicateGroup(
                value=value,
                lead_ids=tuple(lead.id for lead in leads),
                identity_values=tuple(lead.identity_value for lead in leads),
            )
            for value, leads in by_value.items()
            if len(leads) > 1
        ]
        groups.sort(key=lambda g: (-len(g.lead_ids), g.value))
        return groups[:limit]

    # --- merge --------------------------------------------------------------

    async def merge(
        self, *, primary_id: uuid.UUID, merge_ids: Sequence[uuid.UUID]
    ) -> dict[str, Any]:
        """Fold several leads into one, keeping everything that was recorded.

        The merged records are soft-deleted, never dropped (rule 13), and their
        timeline, tasks and labels move to the primary — a merge that discarded
        history would lose the calls that were actually made.

        Values fill *blanks only*. The primary's own data is authoritative;
        anything it lacks is taken from the merged records in the order given,
        which makes the outcome predictable rather than last-write-wins.
        """
        if primary_id in merge_ids:
            raise api_error(422, "invalid_merge", "A lead cannot be merged into itself")
        if not merge_ids:
            raise api_error(422, "invalid_merge", "Name at least one lead to merge in")

        primary = await self._session.get(Lead, primary_id)
        if primary is None or primary.deleted_at is not None:
            raise not_found("Lead")

        rows = await self._session.execute(
            self._session.select(Lead).where(
                Lead.id.in_(list(merge_ids)), Lead.deleted_at.is_(None)
            )
        )
        others = list(rows.scalars().all())
        if len(others) != len(set(merge_ids)):
            raise not_found("Lead")

        fields = await self._fields()
        by_key = {f.key: f for f in fields}

        writer = ActionWriter(self._session, actor_id=self._actor_id)
        await writer.open_changeset(
            source=ChangesetSource.SINGLE_EDIT,
            summary=f"Merged {len(others)} leads into {primary.identity_value}",
            lead_count=len(others) + 1,
        )

        merged_values = dict(primary.values or {})
        deltas: list[FieldDelta] = []

        for other in others:
            for key, value in (other.values or {}).items():
                if merged_values.get(key) in (None, "", [], {}) and value not in (None, ""):
                    deltas.append(
                        FieldDelta(
                            field_key=key,
                            label=by_key[key].label if key in by_key else key,
                            old=merged_values.get(key),
                            new=value,
                        )
                    )
                    merged_values[key] = value

            # History moves rather than dying with the record.
            for model in (Action, Task, LeadLabel):
                await self._session.execute(
                    update(model)
                    .where(
                        model.lead_id == other.id,
                        model.workspace_id == self._session.workspace_id,
                    )
                    .values(lead_id=primary.id)
                )

            other.deleted_at = func.now()
            if other.score:
                primary.score = (primary.score or 0) + other.score

        primary.values = merged_values
        if deltas:
            writer.record_field_changes(primary, deltas)
        primary.search_vector = func.to_tsvector(
            SEARCH_CONFIG,
            search_text_for(merged_values, fields, identity_value=primary.identity_value),
        )
        await self._session.flush()

        return {
            "primary_id": str(primary.id),
            "merged_ids": [str(other.id) for other in others],
            "fields_filled": [delta.field_key for delta in deltas],
            "changeset_id": str(writer.changeset.id),
        }


def _flatten(value: Any) -> str:
    """One cell of a CSV, from whatever shape the field stores.

    Composites become something a human can read rather than raw JSON: a
    spreadsheet full of `{"amount": "5000.00", "currency": "INR"}` is not an
    export anybody can use.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_flatten(item) for item in value)
    if isinstance(value, dict):
        if "amount" in value:
            return f"{value.get('amount', '')} {value.get('currency', '')}".strip()
        if "value" in value:
            return str(value["value"])
        if "next" in value:
            return str(value["next"])
        return ", ".join(str(v) for v in value.values() if v not in (None, ""))
    return str(value)
