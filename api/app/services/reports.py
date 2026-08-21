"""Reports and dashboard aggregates (M9).

`docs/02-api-contract.md` §Dashboard and reports.

**There is no sources report, and there must never be one.** Which field
represents "source" is a per-workspace decision, so `breakdown` takes a
`field_key` and groups by whatever it is given. The handoff is blunt about this:
a fixed sources report *"is the hardcoded-taxonomy mistake in a new costume"*.
The same reasoning makes the leaderboard read `workspaces.leaderboard_metrics`
rather than deciding for itself what a top performer is.

**Every report is a lead read, so every report projects.** A caller's dashboard
must not aggregate a column they cannot see. Grouping by an ungranted field is
refused with the same `unknown_field` the filter compiler uses — identical to a
field that does not exist, so a denial cannot be told apart from an absence.

**Visibility is applied, not assumed.** A manager sees their reports' numbers; a
caller sees their own. That is the same `visible_membership_ids` every list
endpoint uses, and it is why these take the set rather than computing one.

**One pivot, one implementation.** `breakdown` is the generic engine —
`leads_by_stage` and `funnel` are it with different dimensions. PROMPTS.md: *"If
you write a second similar component, stop and generalise the first."*
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.errors import api_error
from app.filters.compiler import _json_path
from app.models import (
    Action,
    Lead,
    LeadField,
    Membership,
    Stage,
    StageKind,
    SystemActionKind,
    User,
    Workspace,
)
from app.permissions import FieldGrants
from app.services.leads import lead_visibility_clause
from app.tenancy.session import ScopedSession

__all__ = [
    "MAX_BREAKDOWN_BUCKETS",
    "MAX_RANGE_DAYS",
    "Bucket",
    "DateRange",
    "LeaderboardRow",
    "ReportService",
]

#: A year at a time. Wider than any dashboard asks for and narrow enough that a
#: pathological range cannot turn a report into a table scan of all history.
MAX_RANGE_DAYS = 366
#: A grouping with more buckets than this is not a chart, it is a list — and
#: rendering 5,000 bars helps nobody. The tail is folded into "Other".
MAX_BREAKDOWN_BUCKETS = 50


@dataclass(frozen=True, slots=True)
class DateRange:
    start: dt.datetime
    end: dt.datetime

    @classmethod
    def of(cls, start: dt.date | None, end: dt.date | None, *, timezone: str) -> DateRange:
        """A closed range in the *workspace's* timezone.

        A report "for August" means August where the sales team sits. Resolving
        it in UTC would put the first and last few hours of the month in the
        wrong bucket, which is invisible until somebody reconciles a month-end
        number against the list that produced it.
        """
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(timezone)
        today = dt.datetime.now(zone).date()
        first = start or (today - dt.timedelta(days=29))
        last = end or today
        if last < first:
            raise api_error(422, "invalid_range", "`to` is before `from`")
        if (last - first).days > MAX_RANGE_DAYS:
            raise api_error(422, "range_too_wide", f"At most {MAX_RANGE_DAYS} days at a time")
        return cls(
            start=dt.datetime.combine(first, dt.time.min, tzinfo=zone),
            # Exclusive upper bound built from the *next* day, so the last day is
            # whole. `23:59:59` would silently drop the final second.
            end=dt.datetime.combine(last + dt.timedelta(days=1), dt.time.min, tzinfo=zone),
        )


@dataclass(frozen=True, slots=True)
class Bucket:
    key: str
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class LeaderboardRow:
    membership_id: uuid.UUID
    name: str
    #: Only the metrics the workspace turned on. See `leaderboard_metrics`.
    metrics: dict[str, int]


class ReportService:
    """Aggregates over leads and actions, for one caller."""

    def __init__(
        self,
        session: ScopedSession,
        *,
        workspace: Workspace,
        grants: FieldGrants,
        visible_membership_ids: frozenset[uuid.UUID],
        sees_all: bool,
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._grants = grants
        self._visible = visible_membership_ids
        self._sees_all = sees_all

    # --- shared scoping ----------------------------------------------------

    def _visibility(self) -> ColumnElement[bool] | None:
        """The same hierarchy rule every list endpoint applies — literally.

        Shared with `LeadService` rather than restated. An earlier version of
        this method restated it and dropped the "unassigned leads are visible
        to everyone" half, so a caller's dashboard counted fewer leads than
        their own list showed and nothing said why. The numbers are the data;
        a report that disagrees with the list is a bug in both.
        """
        return lead_visibility_clause(sees_all=self._sees_all, visible=self._visible)

    def _leads(self, window: DateRange | None = None) -> Select[tuple[Any, ...]]:
        stmt = select(Lead).where(
            Lead.workspace_id == self._session.workspace_id, Lead.deleted_at.is_(None)
        )
        if (clause := self._visibility()) is not None:
            stmt = stmt.where(clause)
        if window is not None:
            stmt = stmt.where(Lead.created_at >= window.start, Lead.created_at < window.end)
        return stmt

    def _actions(self, window: DateRange, assignee_id: uuid.UUID | None = None) -> Any:
        stmt = (
            select(Action)
            .join(Lead, Lead.id == Action.lead_id)
            .where(
                Action.workspace_id == self._session.workspace_id,
                Action.performed_at >= window.start,
                Action.performed_at < window.end,
                Lead.deleted_at.is_(None),
            )
        )
        if (clause := self._visibility()) is not None:
            stmt = stmt.where(clause)
        if assignee_id is not None:
            stmt = stmt.where(Lead.assignee_id == assignee_id)
        return stmt

    # --- the generic pivot -------------------------------------------------

    async def breakdown(
        self,
        *,
        field_key: str,
        window: DateRange | None = None,
        assignee_id: uuid.UUID | None = None,
    ) -> list[Bucket]:
        """Group leads by any field the caller may view.

        The one report that replaces a dozen. "Leads by source", "leads by
        course", "leads by city" are all this, with a different `field_key` —
        because *which* field means "source" is the customer's decision, not the
        product's.
        """
        field = await self._field(field_key)
        accessor = func.coalesce(func.nullif(_json_expr(field.key), ""), "(not set)")

        stmt = self._leads(window).with_only_columns(
            accessor.label("bucket"), func.count().label("total")
        )
        if assignee_id is not None:
            stmt = stmt.where(Lead.assignee_id == assignee_id)
        stmt = (
            stmt.group_by(accessor).order_by(func.count().desc()).limit(MAX_BREAKDOWN_BUCKETS + 1)
        )

        rows = list((await self._session.execute(stmt)).all())
        buckets = [
            Bucket(key=str(value), label=str(value), count=int(count))
            for value, count in rows[:MAX_BREAKDOWN_BUCKETS]
        ]
        if len(rows) > MAX_BREAKDOWN_BUCKETS:
            # Say so rather than truncating silently: a chart missing its tail
            # with no indication is a chart that lies about the total.
            tail = sum(int(count) for _, count in rows[MAX_BREAKDOWN_BUCKETS:])
            buckets.append(Bucket(key="__other__", label="Other", count=tail))
        return buckets

    async def leads_by_stage(self, window: DateRange | None = None) -> list[Bucket]:
        """The pivot with `stage_id` as its dimension."""
        stages = await self._stages()
        stmt = (
            self._leads(window)
            .with_only_columns(Lead.stage_id, func.count())
            .group_by(Lead.stage_id)
        )
        counts = {
            str(stage_id) if stage_id else "null": int(count)
            for stage_id, count in (await self._session.execute(stmt)).all()
        }
        buckets = [
            Bucket(key=str(stage.id), label=stage.label, count=counts.get(str(stage.id), 0))
            for stage in stages
        ]
        if counts.get("null"):
            buckets.append(Bucket(key="null", label="No stage", count=counts["null"]))
        return buckets

    async def funnel(self, window: DateRange | None = None) -> list[Bucket]:
        """Stage counts in pipeline order, won and lost last.

        Not a separate query — the same aggregate as `leads_by_stage`, ordered
        the way a funnel reads. Two orderings of one number is a presentation
        decision, so it does not get its own table scan.
        """
        buckets = await self.leads_by_stage(window)
        stages = {str(stage.id): stage for stage in await self._stages()}

        def rank(bucket: Bucket) -> tuple[int, int]:
            stage = stages.get(bucket.key)
            if stage is None:
                return (3, 0)
            if stage.kind is StageKind.WON:
                return (1, 0)
            if stage.kind is StageKind.LOST:
                return (2, 0)
            return (0, stage.sort_order)

        return sorted(buckets, key=rank)

    # --- activity and calls ------------------------------------------------

    async def activity(self, *, window: DateRange) -> dict[str, int]:
        """Timeline volume by action kind, over the window."""
        stmt = (
            self._actions(window).with_only_columns(Action.kind, func.count()).group_by(Action.kind)
        )
        return {kind.value: int(count) for kind, count in (await self._session.execute(stmt)).all()}

    async def follow_ups(self) -> dict[str, int]:
        """The dashboard's headline numbers: what needs doing.

        Deliberately about *now* rather than the window — an overdue follow-up
        is overdue today whatever range the operator is looking at, and burying
        it inside a date filter is how it gets missed.
        """
        from app.models import Task

        now = dt.datetime.now(dt.UTC)
        base = (
            select(func.count())
            .select_from(Task)
            .where(
                Task.workspace_id == self._session.workspace_id,
                Task.completed_at.is_(None),
            )
        )
        if not self._sees_all and self._visible:
            base = base.where(Task.assignee_id.in_(self._visible))

        late = await self._session.execute(base.where(Task.due_at < now))
        upcoming = await self._session.execute(base.where(Task.due_at >= now))

        stale = self._leads().with_only_columns(func.count()).where(Lead.last_action_at.is_(None))
        never_touched = await self._session.execute(stale)

        return {
            "late": int(late.scalar() or 0),
            "upcoming": int(upcoming.scalar() or 0),
            "never_contacted": int(never_touched.scalar() or 0),
        }

    # --- the leaderboard ---------------------------------------------------

    async def leaderboard(self, *, window: DateRange) -> list[LeaderboardRow]:
        """Per-member totals, honouring the workspace's chosen metrics.

        `leaderboard_metrics` already exists as a column and already has a
        default. Deciding here what "top performer" means would be the same
        mistake as a fixed sources report: it is the customer's definition, and
        a sales team that ranks on calls made and one that ranks on deals won
        are both right about their own business.
        """
        wanted = self._workspace.leaderboard_metrics or {}
        members = await self._members()

        rows: dict[uuid.UUID, dict[str, int]] = {member.id: {} for member in members.values()}

        # Leads created in the window, per assignee. Always present — it is the
        # denominator every other metric is read against.
        created = await self._session.execute(
            self._leads(window)
            .with_only_columns(Lead.assignee_id, func.count())
            .group_by(Lead.assignee_id)
        )
        for assignee_id, count in created.all():
            if assignee_id in rows:
                rows[assignee_id]["leads"] = int(count)

        if wanted.get("stage"):
            won_ids = [stage.id for stage in await self._stages() if stage.kind is StageKind.WON]
            if won_ids:
                won = await self._session.execute(
                    self._leads(window)
                    .with_only_columns(Lead.assignee_id, func.count())
                    .where(Lead.stage_id.in_(won_ids))
                    .group_by(Lead.assignee_id)
                )
                for assignee_id, count in won.all():
                    if assignee_id in rows:
                        rows[assignee_id]["won"] = int(count)

        if wanted.get("rating"):
            rated = await self._session.execute(
                self._leads(window)
                .with_only_columns(Lead.assignee_id, func.avg(Lead.rating))
                .where(Lead.rating.isnot(None))
                .group_by(Lead.assignee_id)
            )
            for assignee_id, average in rated.all():
                if assignee_id in rows and average is not None:
                    rows[assignee_id]["average_rating"] = round(float(average))

        # Calls are always interesting to a telecalling team, and cost one
        # grouped scan of a window already being read.
        calls = await self._session.execute(
            self._actions(window)
            .with_only_columns(Lead.assignee_id, func.count())
            .where(Action.kind == SystemActionKind.CALL_LOGGED)
            .group_by(Lead.assignee_id)
        )
        for assignee_id, count in calls.all():
            if assignee_id in rows:
                rows[assignee_id]["calls"] = int(count)

        return sorted(
            (
                LeaderboardRow(
                    membership_id=membership_id,
                    name=members[membership_id].user.full_name,
                    metrics=metrics,
                )
                for membership_id, metrics in rows.items()
            ),
            key=lambda row: (-sum(row.metrics.values()), row.name),
        )

    # --- lookups -----------------------------------------------------------

    async def _field(self, key: str) -> LeadField:
        """Resolve a field key, refusing anything the caller may not view.

        The refusal is `unknown_field` — identical to a field that does not
        exist — so a denial cannot be told apart from an absence. The filter
        compiler makes the same choice for the same reason.
        """
        rows = await self._session.execute(
            select(LeadField).where(
                LeadField.workspace_id == self._session.workspace_id,
                LeadField.key == key,
            )
        )
        field: LeadField | None = rows.scalars().first()
        if field is None or not self._grants.can_view(field.key):
            raise api_error(422, "unknown_field", f"No field with key {key!r}")
        return field

    async def _stages(self) -> Sequence[Stage]:
        rows = await self._session.execute(
            select(Stage)
            .where(Stage.workspace_id == self._session.workspace_id)
            .order_by(Stage.sort_order)
        )
        return list(rows.scalars().all())

    async def _members(self) -> dict[uuid.UUID, Membership]:
        stmt = (
            select(Membership)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.workspace_id == self._session.workspace_id,
                Membership.is_active.is_(True),
            )
        )
        if not self._sees_all:
            stmt = stmt.where(Membership.id.in_(self._visible or {uuid.uuid4()}))
        # `selectinload` rather than a refresh per row: the leaderboard reads
        # every member's name, and N+1 on the team size is a query per rep.
        rows = await self._session.execute(stmt.options(selectinload(Membership.user)))
        return {member.id: member for member in rows.scalars().all()}


def _json_expr(key: str) -> Any:
    """`leads.values ->> '<key>'` as a literal, so an expression index can match.

    Reuses the filter compiler's accessor rather than building a second one: a
    bind parameter is a Param node, not the Const the index was built with, so
    `values ->> $1` can never use the index the indexed-fields worker created.
    """
    from sqlalchemy import text as sql_text

    return sql_text(_json_path(key))
