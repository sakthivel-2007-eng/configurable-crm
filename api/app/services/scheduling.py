"""Scheduled reports and recurring-date occurrences (M8).

Three things here are easy to get wrong and invisible in a single-timezone,
single-member test — which is why each has its own note.

**Cron is evaluated in the workspace's timezone, not the server's.** "09:00
every weekday" means nine in the morning where the sales team sits. A server in
UTC running a Chennai workspace's schedule would mail it at 14:30 local, and the
customer would report it as "the report comes at the wrong time" without ever
guessing why.

**A report renders as the member who created it.** That is a projection built
from somebody else's grants, which `load_grants` supports and which nothing else
in the codebase does — every other read projects for the caller. Forget it and
you mail a manager's salary column to a caller's inbox. It also means a
schedule whose creator has left is deactivated rather than silently escalated to
full visibility.

**A missed window is caught up once, not once per occurrence.** After a
four-hour outage an hourly schedule has four due occurrences; sending four
identical reports is worse than sending one. The tick asks "was this due at any
point since it last ran", not "how many times".
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from croniter import CroniterBadCronError, croniter
from sqlalchemy import select

from app.errors import api_error
from app.fields.registry import _next_occurrence
from app.models import (
    Lead,
    LeadField,
    LeadFieldType,
    Membership,
    ScheduledReport,
    ScheduledReportFormat,
    Workspace,
)
from app.permissions import FieldProjectionService, load_grants
from app.services.email import Attachment, EmailSender, Outgoing
from app.tenancy.session import ScopedSession

__all__ = [
    "MAX_RECIPIENTS",
    "REPORT_TYPES",
    "Occurrence",
    "RecurringDateService",
    "ScheduledReportService",
    "SchedulerRun",
    "validate_cron",
]

#: The report catalogue M8 can render. M9 owns the rest and extends this — a
#: schedule for a report that does not exist yet would be a row that fails
#: every morning, so the write path refuses it.
REPORT_TYPES: frozenset[str] = frozenset({"leads"})

#: A schedule is an email amplifier. Bounded so a typo cannot turn one row into
#: a mailing list.
MAX_RECIPIENTS = 25


def validate_cron(expression: str) -> str:
    """Reject a cadence that cannot be evaluated, at write time."""
    try:
        croniter(expression)
    except (CroniterBadCronError, ValueError) as exc:
        raise api_error(
            422, "invalid_cron", f"{expression!r} is not a valid cron expression"
        ) from exc
    return expression


def is_due(
    *,
    cron: str,
    timezone: str,
    last_run_at: dt.datetime | None,
    now: dt.datetime,
    catchup_hours: int,
) -> bool:
    """Did this schedule's cron fire between its last run and now?

    Evaluated in `timezone`. `last_run_at` is stored UTC; it is converted, not
    reinterpreted — the difference is the entire bug this function exists to
    avoid.
    """
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)

    floor = local_now - dt.timedelta(hours=catchup_hours)
    since = max(last_run_at.astimezone(zone), floor) if last_run_at else floor

    if since >= local_now:
        return False
    # One question, not a count: "was there an occurrence in the window".
    following = croniter(cron, since).get_next(dt.datetime)
    return bool(following <= local_now)


@dataclass(frozen=True, slots=True)
class SchedulerRun:
    considered: int
    sent: int
    failed: int
    skipped: int


@dataclass(frozen=True, slots=True)
class Occurrence:
    lead_id: uuid.UUID
    identity_value: str
    field_key: str
    occurs_on: dt.date


class ScheduledReportService:
    """CRUD plus the render-and-send that both the cron and `run-now` use."""

    def __init__(
        self,
        session: ScopedSession,
        *,
        workspace: Workspace,
        sender: EmailSender,
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._sender = sender

    async def list_reports(self) -> list[ScheduledReport]:
        rows = await self._session.execute(
            self._session.select(ScheduledReport).order_by(ScheduledReport.name)
        )
        return list(rows.scalars().all())

    async def get(self, report_id: uuid.UUID) -> ScheduledReport:
        report = await self._session.get(ScheduledReport, report_id)
        if report is None:
            raise api_error(404, "report_not_found", "No such scheduled report")
        return report

    async def create(
        self,
        *,
        name: str,
        report_type: str,
        cron: str,
        recipients: Sequence[str],
        params: dict[str, Any] | None = None,
        format: ScheduledReportFormat = ScheduledReportFormat.CSV,
        created_by: uuid.UUID | None,
    ) -> ScheduledReport:
        self._validate(report_type=report_type, cron=cron, recipients=recipients)
        report = ScheduledReport(
            name=name,
            report_type=report_type,
            cron=cron,
            recipients=list(recipients),
            params=params or {},
            format=format,
            created_by=created_by,
        )
        self._session.add(report)
        await self._session.flush()
        return report

    async def update(self, report_id: uuid.UUID, **changes: Any) -> ScheduledReport:
        report = await self.get(report_id)
        self._validate(
            report_type=changes.get("report_type") or report.report_type,
            cron=changes.get("cron") or report.cron,
            recipients=changes.get("recipients") or report.recipients,
        )
        for attribute in (
            "name",
            "report_type",
            "cron",
            "recipients",
            "params",
            "format",
            "is_active",
        ):
            if changes.get(attribute) is not None:
                setattr(report, attribute, changes[attribute])
        await self._session.flush()
        return report

    async def delete(self, report_id: uuid.UUID) -> None:
        report = await self.get(report_id)
        report.is_active = False
        await self._session.flush()

    def _validate(self, *, report_type: str, cron: str, recipients: Sequence[str]) -> None:
        if report_type not in REPORT_TYPES:
            raise api_error(
                422,
                "unknown_report_type",
                f"{report_type!r} is not a report this workspace can schedule "
                f"({', '.join(sorted(REPORT_TYPES))})",
            )
        validate_cron(cron)
        if not recipients:
            raise api_error(422, "no_recipients", "A schedule needs at least one recipient")
        if len(recipients) > MAX_RECIPIENTS:
            raise api_error(422, "too_many_recipients", f"At most {MAX_RECIPIENTS} recipients")

    # --- rendering ---------------------------------------------------------

    async def render(self, report: ScheduledReport) -> Attachment:
        """The report body, projected through its creator's field grants.

        Not the caller's. See the module docstring — this is the one read in the
        product that deliberately uses somebody else's permissions, and getting
        it wrong mails a column the recipient may not see in the UI.
        """
        if report.created_by is None:
            raise api_error(
                422,
                "orphaned_schedule",
                "This schedule's creator is no longer a member, so there are no "
                "field permissions to render it with",
            )

        creator = await self._session.get(Membership, report.created_by)
        if creator is None:
            raise api_error(422, "orphaned_schedule", "This schedule's creator is gone")

        field_rows = await self._session.execute(
            self._session.select(LeadField).order_by(LeadField.sort_order)
        )
        fields = list(field_rows.scalars().all())
        grants = await load_grants(
            self._session,
            template_id=creator.template_id,
            is_admin=await self._is_admin(creator),
            all_field_keys={field.id: field.key for field in fields},
        )
        projection = FieldProjectionService(grants)

        rows = await self._session.execute(
            self._session.select(Lead).where(Lead.deleted_at.is_(None)).limit(10_000)
        )
        leads = list(rows.scalars().all())

        # Export grants, not View: this leaves the product, so it is the same
        # question `/leads/export` asks (rule 3, M7's export path).
        exportable = [field for field in fields if grants.can_export(field.key)]
        header = ["Identity", *[field.label for field in exportable]]

        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(header)
        for lead in leads:
            visible = projection.project_export(lead.values or {})
            writer.writerow([lead.identity_value, *[visible.get(f.key, "") for f in exportable]])

        return Attachment(
            filename=f"{report.name.lower().replace(' ', '-')}.csv",
            content=buffer.getvalue().encode("utf-8"),
            media_type="text/csv",
        )

    async def _is_admin(self, member: Membership) -> bool:
        from app.models import PermissionTemplate

        template = await self._session.get(PermissionTemplate, member.template_id)
        if template is None:  # pragma: no cover - FK guarantees it
            return False
        return bool((template.capabilities or {}).get("leads", {}).get("admin_access"))

    async def send_now(self, report: ScheduledReport, *, now: dt.datetime) -> None:
        """Render and mail. Records the outcome on the row either way."""
        try:
            attachment = await self.render(report)
        except Exception as exc:
            report.last_error = str(exc)[:1000]
            report.last_run_at = now
            await self._session.flush()
            raise

        local = now.astimezone(ZoneInfo(self._workspace.timezone))
        await self._sender.send(
            Outgoing(
                to=tuple(report.recipients),
                subject=f"{report.name} — {local:%Y-%m-%d}",
                body=(
                    f"Attached: {report.name}.\n\n"
                    f"Generated {local:%Y-%m-%d %H:%M %Z} for "
                    f"{self._workspace.name}."
                ),
                attachments=(attachment,),
            )
        )
        report.last_run_at = now
        report.last_error = None
        await self._session.flush()


class RecurringDateService:
    """Upcoming occurrences of a `RECURRING_DATE` field (§0.3, `GET /recurring-dates/occurrences`).

    The greeting scheduler's input. `next` is stored on write and refreshed
    nightly, but it is relative to "today" and so goes stale — this recomputes
    on read rather than trusting it, which is what the registry's own note asks
    for.
    """

    def __init__(self, session: ScopedSession) -> None:
        self._session = session

    async def occurrences(
        self, *, field_key: str, start: dt.date, end: dt.date, limit: int = 200
    ) -> list[Occurrence]:
        if end < start:
            raise api_error(422, "invalid_range", "`to` is before `from`")
        if (end - start).days > 366:
            raise api_error(422, "range_too_wide", "At most one year at a time")

        field = (
            (
                await self._session.execute(
                    self._session.select(LeadField).where(LeadField.key == field_key)
                )
            )
            .scalars()
            .first()
        )
        if field is None:
            raise api_error(404, "unknown_field", f"No field with key {field_key!r}")
        if field.field_type is not LeadFieldType.RECURRING_DATE:
            raise api_error(
                422,
                "not_a_recurring_date",
                f"{field.label!r} is not a recurring date field",
            )

        rows = await self._session.execute(
            self._session.select(Lead)
            .where(
                Lead.deleted_at.is_(None),
                Lead.values[field_key].isnot(None),
            )
            .limit(5_000)
        )

        found: list[Occurrence] = []
        for lead in rows.scalars().all():
            stored = (lead.values or {}).get(field_key)
            if not isinstance(stored, dict) or not stored.get("start"):
                continue
            try:
                anchor = dt.date.fromisoformat(str(stored["start"]))
            except ValueError:  # pragma: no cover - written through the validator
                continue
            occurs = _next_occurrence(
                anchor,
                str(stored.get("frequency") or "YEARLY"),
                int(stored.get("interval") or 1),
                start,
            )
            if start <= occurs <= end:
                found.append(
                    Occurrence(
                        lead_id=lead.id,
                        identity_value=lead.identity_value,
                        field_key=field_key,
                        occurs_on=occurs,
                    )
                )

        found.sort(key=lambda o: (o.occurs_on, o.identity_value))
        return found[:limit]


async def run_due_schedules(
    session: ScopedSession,
    *,
    workspace: Workspace,
    sender: EmailSender,
    now: dt.datetime,
    catchup_hours: int,
) -> SchedulerRun:
    """One workspace's tick. Called by the cron job, and directly by tests."""
    service = ScheduledReportService(session, workspace=workspace, sender=sender)
    rows = await session.execute(
        select(ScheduledReport).where(
            ScheduledReport.workspace_id == workspace.id,
            ScheduledReport.is_active.is_(True),
        )
    )
    reports = list(rows.scalars().all())

    sent = failed = skipped = 0
    for report in reports:
        if not is_due(
            cron=report.cron,
            timezone=workspace.timezone,
            last_run_at=report.last_run_at,
            now=now,
            catchup_hours=catchup_hours,
        ):
            skipped += 1
            continue
        try:
            await service.send_now(report, now=now)
            sent += 1
        except Exception:
            failed += 1

    return SchedulerRun(considered=len(reports), sent=sent, failed=failed, skipped=skipped)
