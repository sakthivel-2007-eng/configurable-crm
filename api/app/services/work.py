"""Tasks and labels (M7).

Both are small, and both have one decision in them worth stating.

**A task's bucket is computed, never stored.** `late` means "due before now and
not done", and *now* moves. Storing the bucket would need a nightly job to
shuffle rows between them, and every morning there would be a window where the
list was wrong. Buckets are resolved against the **workspace's** timezone
(architecture rule 10), so a task due "today" is due on the customer's today.

**A task on a lead writes to the lead's timeline.** "Someone promised to call
back on Thursday" belongs in the audit trail as much as the call does, so
creating and completing a task each open a changeset and append an action —
rule 5a applies here exactly as it does to an edit.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.sql.elements import ColumnElement

from app.errors import conflict, not_found, unprocessable
from app.models.enums import ChangesetSource
from app.models.lead import Lead
from app.models.work import Label, LeadLabel, Task
from app.models.workspace import Membership, Workspace
from app.services.actions import ActionWriter
from app.tenancy.session import ScopedSession

__all__ = ["MAX_LABELS", "LabelService", "TaskBucket", "TaskService"]

#: A workspace with hundreds of labels has a taxonomy problem it should be
#: solving with a DROPDOWN field instead. Bounded so the picker stays usable.
MAX_LABELS = 100


class TaskBucket(enum.StrEnum):
    """The three lists the API contract names."""

    UPCOMING = "upcoming"
    LATE = "late"
    DONE = "done"


class TaskService:
    """CRUD for tasks, plus the bucket queries the list view is built on."""

    def __init__(
        self,
        session: ScopedSession,
        *,
        workspace: Workspace,
        actor_id: uuid.UUID | None,
        visible_membership_ids: frozenset[uuid.UUID],
        sees_all: bool,
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._actor_id = actor_id
        self._visible = visible_membership_ids
        self._sees_all = sees_all

    def _now(self) -> dt.datetime:
        """This instant, but *thought about* in the workspace's timezone.

        The comparison itself is between instants, so the zone only matters for
        the day boundaries a caller reasons about — which is exactly why it must
        be the customer's zone and not the server's.
        """
        return dt.datetime.now(ZoneInfo(self._workspace.timezone))

    def _visibility_clause(self) -> ColumnElement[bool] | None:
        """A manager sees their reports' tasks; a caller sees their own.

        An unassigned task is visible to everyone, for the same reason an
        unassigned lead is: it belongs to nobody yet, and hiding it would mean
        work nobody could pick up.
        """
        if self._sees_all:
            return None
        return Task.assignee_id.in_(self._visible) | Task.assignee_id.is_(None)

    def _bucket_clause(self, bucket: TaskBucket) -> ColumnElement[bool]:
        now = self._now()
        if bucket is TaskBucket.DONE:
            return Task.completed_at.is_not(None)
        if bucket is TaskBucket.LATE:
            return (Task.completed_at.is_(None)) & (Task.due_at < now)
        return (Task.completed_at.is_(None)) & (Task.due_at >= now)

    async def list_tasks(
        self,
        *,
        limit: int,
        offset: int,
        bucket: TaskBucket | None = None,
        assignee_id: uuid.UUID | None = None,
        lead_id: uuid.UUID | None = None,
    ) -> tuple[Sequence[Task], int]:
        statement = self._session.select(Task)
        if (visibility := self._visibility_clause()) is not None:
            statement = statement.where(visibility)
        if bucket is not None:
            statement = statement.where(self._bucket_clause(bucket))
        if assignee_id is not None:
            statement = statement.where(Task.assignee_id == assignee_id)
        if lead_id is not None:
            statement = statement.where(Task.lead_id == lead_id)

        total = await self._session.execute(select(func.count()).select_from(statement.subquery()))
        # Soonest first: a task list is a queue, and the top of it is what
        # someone is about to do.
        rows = await self._session.execute(
            statement.order_by(Task.due_at, Task.id).limit(limit).offset(offset)
        )
        return rows.scalars().all(), int(total.scalar_one())

    async def counts(self) -> dict[str, int]:
        """One number per bucket, for the badge on the nav item."""
        result: dict[str, int] = {}
        for bucket in TaskBucket:
            statement = self._session.select(Task).where(self._bucket_clause(bucket))
            if (visibility := self._visibility_clause()) is not None:
                statement = statement.where(visibility)
            total = await self._session.execute(
                select(func.count()).select_from(statement.subquery())
            )
            result[bucket.value] = int(total.scalar_one())
        return result

    async def get_task(self, task_id: uuid.UUID) -> Task:
        rows = await self._session.execute(
            self._session.select(Task).where(Task.id == task_id).limit(1)
        )
        task: Task | None = rows.scalar_one_or_none()
        if task is None:
            raise not_found("Task")
        if not self._sees_all and not (
            task.assignee_id is None or task.assignee_id in self._visible
        ):
            # Someone else's work, and indistinguishable from absent by design —
            # a 403 here would confirm the task exists.
            raise not_found("Task")
        return task

    async def _lead(self, lead_id: uuid.UUID) -> Lead:
        lead = await self._session.get(Lead, lead_id)
        if lead is None or lead.deleted_at is not None:
            raise not_found("Lead")
        return lead

    async def _assert_member(self, membership_id: uuid.UUID) -> None:
        if await self._session.get(Membership, membership_id) is None:
            raise not_found("Member")

    async def create_task(
        self,
        *,
        title: str,
        due_at: dt.datetime,
        lead_id: uuid.UUID | None = None,
        notes: str | None = None,
        assignee_id: uuid.UUID | None = None,
    ) -> Task:
        if assignee_id is not None:
            await self._assert_member(assignee_id)

        lead = await self._lead(lead_id) if lead_id is not None else None

        task = Task(
            lead_id=lead.id if lead else None,
            title=title,
            notes=notes,
            due_at=due_at,
            assignee_id=assignee_id,
            created_by_id=self._actor_id,
        )
        self._session.add(task)
        await self._session.flush()

        if lead is not None:
            writer = ActionWriter(self._session, actor_id=self._actor_id)
            await writer.open_changeset(
                source=ChangesetSource.SINGLE_EDIT,
                summary=f"Added task: {title}",
                lead_count=1,
            )
            writer.record_task(lead, completed=False, task_id=task.id, title=title, due_at=due_at)
            await self._session.flush()
        return task

    async def update_task(
        self,
        task_id: uuid.UUID,
        *,
        title: str | None = None,
        notes: str | None = None,
        due_at: dt.datetime | None = None,
        assignee_id: uuid.UUID | None = None,
        assignee_given: bool = False,
    ) -> Task:
        task = await self.get_task(task_id)
        if task.completed_at is not None:
            raise conflict(
                "task_completed",
                "A completed task cannot be edited. Reopen it first.",
            )
        if title is not None:
            task.title = title
        if notes is not None:
            task.notes = notes
        if due_at is not None:
            task.due_at = due_at
        if assignee_given:
            if assignee_id is not None:
                await self._assert_member(assignee_id)
            task.assignee_id = assignee_id
        await self._session.flush()
        return task

    async def complete_task(self, task_id: uuid.UUID) -> Task:
        task = await self.get_task(task_id)
        if task.completed_at is not None:
            raise conflict("task_completed", "That task is already done")

        task.completed_at = dt.datetime.now(dt.UTC)
        task.completed_by_id = self._actor_id

        if task.lead_id is not None:
            lead = await self._lead(task.lead_id)
            writer = ActionWriter(self._session, actor_id=self._actor_id)
            await writer.open_changeset(
                source=ChangesetSource.SINGLE_EDIT,
                summary=f"Completed task: {task.title}",
                lead_count=1,
            )
            writer.record_task(lead, completed=True, task_id=task.id, title=task.title)
        await self._session.flush()
        return task

    async def reopen_task(self, task_id: uuid.UUID) -> Task:
        task = await self.get_task(task_id)
        task.completed_at = None
        task.completed_by_id = None
        await self._session.flush()
        return task


class LabelService:
    """Free-form tags on leads.

    Deliberately not a `TAGS` field. A field is part of the schema an admin
    designed and is subject to the field matrix; a label is something a caller
    sticks on in the moment. Both exist in the source product and conflating
    them would mean either putting ad-hoc tags through the permission matrix or
    letting anyone add options to a designed field.
    """

    def __init__(self, session: ScopedSession) -> None:
        self._session = session

    async def list_labels(self, *, include_archived: bool = False) -> Sequence[Label]:
        statement = self._session.select(Label)
        if not include_archived:
            statement = statement.where(Label.is_archived.is_(False))
        rows = await self._session.execute(statement.order_by(Label.sort_order, Label.name))
        labels: Sequence[Label] = rows.scalars().all()
        return labels

    async def get_label(self, label_id: uuid.UUID) -> Label:
        label = await self._session.get(Label, label_id)
        if label is None:
            raise not_found("Label")
        return label

    async def create_label(self, *, name: str, color: str | None = None) -> Label:
        existing = await self.list_labels(include_archived=True)
        if len(existing) >= MAX_LABELS:
            raise unprocessable(
                "label_limit",
                f"A workspace may keep at most {MAX_LABELS} labels",
            )
        if any(label.name.lower() == name.lower() for label in existing):
            raise conflict("duplicate_label", f"A label called {name!r} already exists")

        label = Label(name=name, color=color, sort_order=len(existing))
        self._session.add(label)
        await self._session.flush()
        return label

    async def update_label(
        self, label_id: uuid.UUID, *, name: str | None = None, color: str | None = None
    ) -> Label:
        label = await self.get_label(label_id)
        if name is not None:
            clash = [
                other
                for other in await self.list_labels(include_archived=True)
                if other.id != label.id and other.name.lower() == name.lower()
            ]
            if clash:
                raise conflict("duplicate_label", f"A label called {name!r} already exists")
            label.name = name
        if color is not None:
            label.color = color
        await self._session.flush()
        return label

    async def archive_label(self, label_id: uuid.UUID) -> Label:
        """Archived, never deleted (rule 13).

        Deleting would silently strip the label from every lead carrying it,
        and "why did those leads lose their tag" is unanswerable afterwards.
        """
        label = await self.get_label(label_id)
        label.is_archived = True
        await self._session.flush()
        return label

    async def labels_for(self, lead_id: uuid.UUID) -> Sequence[Label]:
        rows = await self._session.execute(
            self._session.select(Label)
            .join(LeadLabel, LeadLabel.label_id == Label.id)
            .where(LeadLabel.lead_id == lead_id)
            .order_by(Label.sort_order, Label.name)
        )
        labels: Sequence[Label] = rows.scalars().all()
        return labels

    async def attach(self, lead_id: uuid.UUID, label_id: uuid.UUID) -> Sequence[Label]:
        lead = await self._session.get(Lead, lead_id)
        if lead is None or lead.deleted_at is not None:
            raise not_found("Lead")
        label = await self.get_label(label_id)
        if label.is_archived:
            raise conflict("label_archived", f"{label.name} is archived and cannot be applied")

        already = await self._session.execute(
            self._session.select(LeadLabel).where(
                LeadLabel.lead_id == lead_id, LeadLabel.label_id == label_id
            )
        )
        if already.scalar_one_or_none() is None:
            self._session.add(LeadLabel(lead_id=lead_id, label_id=label_id))
            await self._session.flush()
        return await self.labels_for(lead_id)

    async def detach(self, lead_id: uuid.UUID, label_id: uuid.UUID) -> Sequence[Label]:
        # A genuine deletion, and a justified one: a lead-label link is a fact
        # that can be withdrawn, not a record of something that happened. Rule
        # 13 protects leads and actions, not a join row nobody audits.
        #
        # `ScopedSession` has no `delete()` by design, so the statement names
        # `workspace_id` itself — the loader criteria only applies to SELECT,
        # which is the same reason `undeclare_indexed` spells it out.
        await self._session.execute(
            delete(LeadLabel).where(
                LeadLabel.lead_id == lead_id,
                LeadLabel.label_id == label_id,
                LeadLabel.workspace_id == self._session.workspace_id,
            )
        )
        await self._session.flush()
        return await self.labels_for(lead_id)
