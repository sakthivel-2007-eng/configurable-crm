"""Sales groups, assignment rules, and redistribution (M8).

The CRUD around `AssignmentEngine`. Three services, kept in one module because
they are one idea — *who gets which leads* — and splitting them would mean three
files that only ever change together.

`DistributionService` is the odd one: it is a lead write, not configuration, so
it opens a changeset like every other mutation and the whole redistribution can
be undone from the M7 edit report as a unit. Moving 500 leads onto the wrong rep
is exactly the kind of mistake somebody needs to take back.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select

from app.errors import api_error
from app.models import (
    AssignmentCursor,
    AssignmentRule,
    AssignmentStrategy,
    Changeset,
    ChangesetSource,
    Lead,
    Membership,
    SalesGroup,
    SalesGroupMember,
    Workspace,
)
from app.services.actions import ActionWriter
from app.services.assignment import (
    MAX_WEIGHT,
    AssignmentEngine,
    AssignmentOutcome,
    validate_strategy_config,
)
from app.tenancy.session import ScopedSession

__all__ = [
    "MAX_DISTRIBUTE_LEADS",
    "AssignmentRuleService",
    "DistributionResult",
    "DistributionService",
    "SalesGroupService",
]

#: One redistribution is one changeset, and one changeset has to stay small
#: enough to preview and undo. Same ceiling as a bulk edit, for the same reason.
MAX_DISTRIBUTE_LEADS = 500


class SalesGroupService:
    """CRUD for `sales_groups` and their weighted membership."""

    def __init__(self, session: ScopedSession) -> None:
        self._session = session

    async def list_groups(self, *, include_archived: bool = False) -> list[SalesGroup]:
        stmt = self._session.select(SalesGroup).order_by(SalesGroup.name)
        if not include_archived:
            stmt = stmt.where(SalesGroup.is_archived.is_(False))
        return list((await self._session.execute(stmt)).scalars().all())

    async def get(self, group_id: uuid.UUID) -> SalesGroup:
        group = await self._session.get(SalesGroup, group_id)
        if group is None:
            raise api_error(404, "group_not_found", "No such sales group")
        return group

    async def create(self, *, name: str, description: str | None) -> SalesGroup:
        await self._assert_name_free(name)
        group = SalesGroup(name=name, description=description)
        self._session.add(group)
        await self._session.flush()
        return group

    async def update(
        self,
        group_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        is_archived: bool | None = None,
    ) -> SalesGroup:
        group = await self.get(group_id)
        if name is not None and name != group.name:
            await self._assert_name_free(name)
            group.name = name
        if description is not None:
            group.description = description
        if is_archived is not None:
            group.is_archived = is_archived
        await self._session.flush()
        return group

    async def archive(self, group_id: uuid.UUID) -> None:
        """Archive, never delete — a rule may still point at it (rule 13).

        A hard delete would cascade the membership rows away and leave any
        `SALES_GROUP` rule silently assigning nobody. Archiving keeps the rule
        legible: the settings screen can say the group it targets is archived.
        """
        group = await self.get(group_id)
        group.is_archived = True
        await self._session.flush()

    async def members(self, group_id: uuid.UUID) -> list[SalesGroupMember]:
        await self.get(group_id)
        rows = await self._session.execute(
            self._session.select(SalesGroupMember)
            .where(SalesGroupMember.group_id == group_id)
            .order_by(SalesGroupMember.membership_id)
        )
        return list(rows.scalars().all())

    async def set_members(
        self, group_id: uuid.UUID, rows: Sequence[dict[str, Any]]
    ) -> list[SalesGroupMember]:
        """Replace the whole membership. PUT semantics, per the contract."""
        await self.get(group_id)

        seen: set[uuid.UUID] = set()
        cleaned: list[tuple[uuid.UUID, int]] = []
        for row in rows:
            membership_id = uuid.UUID(str(row["membership_id"]))
            if membership_id in seen:
                raise api_error(422, "duplicate_member", "A member appears twice in the group")
            seen.add(membership_id)
            weight = int(row.get("weight", 1))
            if not 1 <= weight <= MAX_WEIGHT:
                raise api_error(422, "invalid_weight", f"Weight must be 1..{MAX_WEIGHT}")
            cleaned.append((membership_id, weight))

        if cleaned:
            found = await self._session.execute(
                self._session.select(Membership).where(Membership.id.in_([m for m, _ in cleaned]))
            )
            known = {m.id for m in found.scalars().all()}
            missing = seen - known
            if missing:
                # Scoped lookup, so a membership from another workspace reads as
                # "not a member here" rather than confirming it exists.
                raise api_error(
                    422,
                    "unknown_member",
                    f"Not members of this workspace: {sorted(map(str, missing))}",
                )

        await self._session.execute(
            delete(SalesGroupMember).where(
                SalesGroupMember.group_id == group_id,
                SalesGroupMember.workspace_id == self._session.workspace_id,
            )
        )
        for membership_id, weight in cleaned:
            self._session.add(
                SalesGroupMember(group_id=group_id, membership_id=membership_id, weight=weight)
            )
        await self._session.flush()
        return await self.members(group_id)

    async def _assert_name_free(self, name: str) -> None:
        existing = await self._session.execute(
            self._session.select(SalesGroup).where(func.lower(SalesGroup.name) == name.lower())
        )
        if existing.scalars().first() is not None:
            raise api_error(409, "duplicate_group", f"A group named {name!r} already exists")


class AssignmentRuleService:
    """CRUD for `assignment_rules`, plus the dry-run preview."""

    def __init__(self, session: ScopedSession, *, workspace: Workspace) -> None:
        self._session = session
        self._workspace = workspace

    async def list_rules(self) -> list[AssignmentRule]:
        rows = await self._session.execute(
            self._session.select(AssignmentRule).order_by(
                AssignmentRule.priority, AssignmentRule.id
            )
        )
        return list(rows.scalars().all())

    async def get(self, rule_id: uuid.UUID) -> AssignmentRule:
        rule = await self._session.get(AssignmentRule, rule_id)
        if rule is None:
            raise api_error(404, "rule_not_found", "No such assignment rule")
        return rule

    async def create(
        self,
        *,
        name: str,
        strategy: AssignmentStrategy,
        config: dict[str, Any],
        conditions: dict[str, Any],
        priority: int | None = None,
        skip_unavailable: bool = True,
        is_active: bool = True,
    ) -> AssignmentRule:
        await self._assert_name_free(name)
        rule = AssignmentRule(
            name=name,
            strategy=strategy,
            config=validate_strategy_config(strategy, config),
            conditions=conditions or {},
            priority=priority if priority is not None else await self._next_priority(),
            skip_unavailable=skip_unavailable,
            is_active=is_active,
        )
        self._session.add(rule)
        await self._session.flush()
        await self._assert_compiles(rule)
        return rule

    async def update(self, rule_id: uuid.UUID, **changes: Any) -> AssignmentRule:
        rule = await self.get(rule_id)
        name = changes.get("name")
        if name is not None and name != rule.name:
            await self._assert_name_free(name)
            rule.name = name

        strategy = changes.get("strategy") or rule.strategy
        if "strategy" in changes or "config" in changes:
            config = changes.get("config")
            rule.strategy = strategy
            rule.config = validate_strategy_config(
                strategy, config if config is not None else rule.config
            )
            # A changed strategy means the old cursor position means nothing —
            # it indexes a deal list that no longer exists.
            await self._session.execute(
                delete(AssignmentCursor).where(
                    AssignmentCursor.rule_id == rule.id,
                    AssignmentCursor.workspace_id == self._session.workspace_id,
                )
            )

        for field in ("conditions", "priority", "skip_unavailable", "is_active"):
            if changes.get(field) is not None:
                setattr(rule, field, changes[field])

        await self._session.flush()
        await self._assert_compiles(rule)
        return rule

    async def delete(self, rule_id: uuid.UUID) -> None:
        """Deactivate rather than delete.

        A rule that has fired is part of the explanation for where existing
        leads went. Removing the row deletes that explanation.
        """
        rule = await self.get(rule_id)
        rule.is_active = False
        await self._session.flush()

    async def reorder(self, order: Sequence[uuid.UUID]) -> list[AssignmentRule]:
        rules = await self.list_rules()
        known = {rule.id: rule for rule in rules}
        if set(order) != set(known):
            raise api_error(
                422,
                "incomplete_order",
                "Reordering must list every rule exactly once",
            )
        for index, rule_id in enumerate(order):
            known[rule_id].priority = index
        await self._session.flush()
        return await self.list_rules()

    async def preview(self, lead_id: uuid.UUID) -> AssignmentOutcome:
        """Which rule would fire for an existing lead, and who it would pick.

        Deliberately reuses `AssignmentEngine.decide`, so a preview cannot drift
        from the real thing. It does advance a round-robin cursor, which is why
        the endpoint documents that and why the UI calls it on demand rather
        than on every keystroke — a preview that *avoided* the cursor would be
        previewing different behaviour from the one that runs.
        """
        lead = await self._session.get(Lead, lead_id)
        if lead is None:
            raise api_error(404, "lead_not_found", "No such lead")
        engine = AssignmentEngine(self._session, workspace=self._workspace)
        return await engine.decide(lead)

    async def _next_priority(self) -> int:
        highest = await self._session.execute(
            select(func.max(AssignmentRule.priority)).where(
                AssignmentRule.workspace_id == self._session.workspace_id
            )
        )
        return int((highest.scalar() or -1) + 1)

    async def _assert_compiles(self, rule: AssignmentRule) -> None:
        """Reject conditions the engine could not evaluate, at write time.

        A rule whose filter is malformed is not a rule — it is a lead-create
        path that raises at 3am. `_parse` raises the same 422 the filter
        endpoints raise, so the message is one the settings UI already renders.
        """
        engine = AssignmentEngine(self._session, workspace=self._workspace)
        engine._parse(rule.conditions)

    async def _assert_name_free(self, name: str) -> None:
        existing = await self._session.execute(
            self._session.select(AssignmentRule).where(
                func.lower(AssignmentRule.name) == name.lower()
            )
        )
        if existing.scalars().first() is not None:
            raise api_error(409, "duplicate_rule", f"A rule named {name!r} already exists")


@dataclass(frozen=True, slots=True)
class DistributionResult:
    changeset_id: uuid.UUID | None
    assigned: int
    skipped: int
    total: int


class DistributionService:
    """Redistribute an existing, filtered set of leads (§M8, `/leads/distribute`)."""

    def __init__(
        self,
        session: ScopedSession,
        *,
        workspace: Workspace,
        actor_id: uuid.UUID | None,
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._actor_id = actor_id

    async def distribute(
        self,
        *,
        lead_ids: Sequence[uuid.UUID],
        strategy: AssignmentStrategy,
        config: dict[str, Any],
        skip_unavailable: bool = True,
    ) -> DistributionResult:
        if not lead_ids:
            raise api_error(422, "no_leads", "Nothing to distribute")
        if len(lead_ids) > MAX_DISTRIBUTE_LEADS:
            raise api_error(
                422,
                "too_many_leads",
                f"Distribute at most {MAX_DISTRIBUTE_LEADS} leads at a time",
            )

        config = validate_strategy_config(strategy, config)

        rows = await self._session.execute(
            self._session.select(Lead).where(Lead.id.in_(list(lead_ids)), Lead.deleted_at.is_(None))
        )
        leads = list(rows.scalars().all())
        if not leads:
            raise api_error(404, "no_leads_found", "None of those leads exist here")

        # A throwaway rule, never persisted, so distribution reuses the same
        # strategy resolution the create path uses instead of a parallel one.
        rule = AssignmentRule(
            workspace_id=self._session.workspace_id,
            name="__distribution__",
            strategy=strategy,
            config=config,
            conditions={},
            skip_unavailable=skip_unavailable,
        )
        engine = AssignmentEngine(self._session, workspace=self._workspace)

        writer = ActionWriter(self._session, actor_id=self._actor_id)
        await writer.open_changeset(
            source=ChangesetSource.DISTRIBUTION,
            summary=f"Distributed {len(leads)} leads",
            lead_count=len(leads),
        )

        assigned = 0
        skipped = 0
        # Deal list computed once: recomputing per lead would re-read the roster
        # 500 times and, worse, let a mid-run availability change silently split
        # one distribution into two different policies.
        deal = await engine._candidates(rule)
        if not deal and strategy not in (
            AssignmentStrategy.FIXED,
            AssignmentStrategy.FIELD_VALUE,
            AssignmentStrategy.UNASSIGNED,
        ):
            raise api_error(422, "no_candidates", "No eligible member to distribute to")

        for index, lead in enumerate(leads):
            target = await self._target(engine, rule, lead, deal, index)
            if target == lead.assignee_id:
                skipped += 1
                continue
            writer.record_assignment_change(
                lead, old_assignee_id=lead.assignee_id, new_assignee_id=target
            )
            lead.assignee_id = target
            assigned += 1

        await self._session.flush()
        changeset_id = writer.changeset.id if isinstance(writer.changeset, Changeset) else None
        return DistributionResult(
            changeset_id=changeset_id,
            assigned=assigned,
            skipped=skipped,
            total=len(leads),
        )

    async def _target(
        self,
        engine: AssignmentEngine,
        rule: AssignmentRule,
        lead: Lead,
        deal: Sequence[uuid.UUID],
        index: int,
    ) -> uuid.UUID | None:
        if rule.strategy is AssignmentStrategy.UNASSIGNED:
            return None
        if rule.strategy is AssignmentStrategy.FIXED:
            engine._lead_under_evaluation = lead
            return await engine._resolve(rule)
        if rule.strategy is AssignmentStrategy.FIELD_VALUE:
            engine._lead_under_evaluation = lead
            return await engine._resolve_field_value(rule)
        # Round-robin over a fixed list: deal straight round it. No cursor row,
        # because this rule does not exist and will never fire again.
        return deal[index % len(deal)]
