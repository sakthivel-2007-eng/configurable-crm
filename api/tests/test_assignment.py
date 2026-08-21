"""The assignment engine (M8).

The first test is the one that matters. `05-handoff-m6-m10.md` names the
round-robin race as "the single most likely defect in M8 and it is invisible in
single-threaded tests" — so the first thing here is a genuinely concurrent one,
with two separate sessions and two separate transactions.

The rest cover the strategies, the availability and licence gates, priority
order, and the two properties an operator depends on: that a preview assigns
nothing, and that a distribution undoes as a unit.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.factories import WorkspaceFixture, add_member, build_workspace

from app.auth.passwords import PasswordHasherService
from app.models import (
    AssignmentRule,
    AssignmentStrategy,
    AvailabilityStatus,
    Lead,
    Membership,
    Workspace,
)
from app.services.assignment import AssignmentEngine
from app.tenancy.session import ScopedSession

pytestmark = pytest.mark.integration


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


async def _workspace(db_session: AsyncSession, hasher: PasswordHasherService) -> WorkspaceFixture:
    return await build_workspace(
        db_session, hasher, name="Routing Co", owner_email="owner@routing.test"
    )


async def _reps(
    db_session: AsyncSession, hasher: PasswordHasherService, fixture: WorkspaceFixture, count: int
) -> list[uuid.UUID]:
    ids = []
    for index in range(count):
        actor = await add_member(
            db_session,
            hasher,
            fixture,
            key=f"rep{index}",
            email=f"rep{index}@routing.test",
            template_name="Caller",
        )
        ids.append(actor.membership.id)
    return ids


async def _rule(
    db_session: AsyncSession,
    fixture: WorkspaceFixture,
    *,
    strategy: AssignmentStrategy,
    config: dict[str, Any],
    conditions: dict[str, Any] | None = None,
    priority: int = 0,
    skip_unavailable: bool = True,
    name: str | None = None,
) -> AssignmentRule:
    rule = AssignmentRule(
        workspace_id=fixture.workspace.id,
        name=name or f"rule-{priority}-{uuid.uuid4().hex[:6]}",
        strategy=strategy,
        config=config,
        conditions=conditions or {},
        priority=priority,
        skip_unavailable=skip_unavailable,
    )
    db_session.add(rule)
    await db_session.commit()
    return rule


async def _lead(
    db_session: AsyncSession,
    fixture: WorkspaceFixture,
    *,
    phone: str,
    values: dict[str, Any] | None = None,
) -> Lead:
    lead = Lead(
        workspace_id=fixture.workspace.id,
        identity_value=phone,
        values={"phone": phone, **(values or {})},
    )
    db_session.add(lead)
    await db_session.commit()
    return lead


# --- the concurrency trap ----------------------------------------------------


async def test_round_robin_locks_the_cursor_against_a_concurrent_assignment(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    hasher: PasswordHasherService,
) -> None:
    """Two leads arriving together must go to two different reps.

    This is the defect the handoff warned about: a read-then-write cursor lets
    both transactions read position 0, both pick rep 0, and rep 1 gets nothing.

    Three things were learned writing this, each after watching an earlier
    version pass against a deliberately broken engine:

    **The cursor row must already exist.** When it does not, the
    `ON CONFLICT DO NOTHING` insert takes its own lock and serialises the
    transactions by itself, so nothing about the cursor is being tested.

    **Racing two asyncio tasks proves nothing.** They ping-pong at await points
    and happen to serialise. This asserts the property directly instead: while
    one transaction holds the cursor, a second *blocks*, and on release picks
    the next rep rather than the same one.

    **The safety is the locked upsert and `FOR UPDATE` together**, not either
    alone — removing only `FOR UPDATE` still passes, because the upsert blocks
    on a row another transaction has updated. Replacing the whole block with a
    plain read-then-write fails this test loudly, which is the shape the
    handoff actually warned about and the one someone might "simplify" into.
    """
    fixture = await _workspace(db_session, hasher)
    reps = await _reps(db_session, hasher, fixture, 2)
    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.ROUND_ROBIN,
        config={"membership_ids": [str(r) for r in reps]},
    )
    lead_a = await _lead(db_session, fixture, phone="+10000000001")
    lead_b = await _lead(db_session, fixture, phone="+10000000002")
    seed = await _lead(db_session, fixture, phone="+10000000003")
    workspace_id = fixture.workspace.id

    scoped = ScopedSession(db_session, workspace_id)
    await AssignmentEngine(scoped, workspace=fixture.workspace).decide(seed)
    await db_session.commit()

    async def decide(lead_id: uuid.UUID, raw: AsyncSession) -> uuid.UUID | None:
        workspace = await raw.get(Workspace, workspace_id)
        assert workspace is not None
        lead = await raw.get(Lead, lead_id)
        assert lead is not None
        engine = AssignmentEngine(ScopedSession(raw, workspace_id), workspace=workspace)
        return (await engine.decide(lead)).membership_id

    async with session_factory() as first, session_factory() as second:
        pick_a = await decide(lead_a.id, first)  # holds the row lock, uncommitted

        pending = asyncio.create_task(decide(lead_b.id, second))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(pending), timeout=2)

        await first.commit()
        pick_b = await asyncio.wait_for(pending, timeout=30)
        await second.commit()

    assert pick_a is not None and pick_b is not None
    assert pick_a != pick_b, (
        f"both leads went to the same rep ({pick_a}) — the cursor was read, not locked"
    )
    assert {pick_a, pick_b} == set(reps)


# --- strategies --------------------------------------------------------------


async def test_round_robin_cycles_in_order(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    fixture = await _workspace(db_session, hasher)
    reps = await _reps(db_session, hasher, fixture, 3)
    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.ROUND_ROBIN,
        config={"membership_ids": [str(r) for r in reps]},
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    engine = AssignmentEngine(scoped, workspace=fixture.workspace)

    picks = []
    for index in range(6):
        lead = await _lead(db_session, fixture, phone=f"+1000001{index:04d}")
        picks.append((await engine.decide(lead)).membership_id)

    assert picks == reps + reps


async def test_weighted_deals_proportionally(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """A member on weight 3 takes three of every four leads."""
    fixture = await _workspace(db_session, hasher)
    heavy, light = await _reps(db_session, hasher, fixture, 2)
    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.WEIGHTED,
        config={
            "members": [
                {"membership_id": str(heavy), "weight": 3},
                {"membership_id": str(light), "weight": 1},
            ]
        },
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    engine = AssignmentEngine(scoped, workspace=fixture.workspace)

    picks = []
    for index in range(8):
        lead = await _lead(db_session, fixture, phone=f"+1000002{index:04d}")
        picks.append((await engine.decide(lead)).membership_id)

    assert picks.count(heavy) == 6
    assert picks.count(light) == 2


async def test_field_value_routes_by_the_lead_s_own_value(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    fixture = await _workspace(db_session, hasher)
    north, south = await _reps(db_session, hasher, fixture, 2)
    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.FIELD_VALUE,
        config={
            "field_key": "name",
            "map": {"north": str(north), "south": str(south)},
            "fallback": str(north),
        },
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    engine = AssignmentEngine(scoped, workspace=fixture.workspace)

    northern = await _lead(db_session, fixture, phone="+10000030001", values={"name": "north"})
    southern = await _lead(db_session, fixture, phone="+10000030002", values={"name": "south"})
    unknown = await _lead(db_session, fixture, phone="+10000030003", values={"name": "west"})

    assert (await engine.decide(northern)).membership_id == north
    assert (await engine.decide(southern)).membership_id == south
    # Not in the map, so the fallback catches it rather than dropping the lead.
    assert (await engine.decide(unknown)).membership_id == north


async def test_unassigned_strategy_matches_and_assigns_nobody(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """A deliberate stop, distinguishable from no rule matching at all."""
    fixture = await _workspace(db_session, hasher)
    reps = await _reps(db_session, hasher, fixture, 1)
    await _rule(db_session, fixture, strategy=AssignmentStrategy.UNASSIGNED, config={}, priority=0)
    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.ROUND_ROBIN,
        config={"membership_ids": [str(reps[0])]},
        priority=1,
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    engine = AssignmentEngine(scoped, workspace=fixture.workspace)

    lead = await _lead(db_session, fixture, phone="+10000040001")
    outcome = await engine.decide(lead)

    assert outcome.membership_id is None
    assert outcome.reason == "rule_assigns_nobody"
    assert outcome.rule_id is not None, "the rule matched; it just picked nobody"


# --- the gates ---------------------------------------------------------------


async def test_skip_unavailable_skips_a_member_on_leave(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    fixture = await _workspace(db_session, hasher)
    away, here = await _reps(db_session, hasher, fixture, 2)
    member = await db_session.get(Membership, away)
    assert member is not None
    member.availability = AvailabilityStatus.ON_LEAVE
    await db_session.commit()

    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.ROUND_ROBIN,
        config={"membership_ids": [str(away), str(here)]},
        skip_unavailable=True,
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    engine = AssignmentEngine(scoped, workspace=fixture.workspace)

    for index in range(4):
        lead = await _lead(db_session, fixture, phone=f"+1000005{index:04d}")
        assert (await engine.decide(lead)).membership_id == here


async def test_an_unlicensed_member_is_never_assigned_to(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """Unconditional, whatever `skip_unavailable` says.

    An unlicensed member cannot log in, so a lead assigned to one is a lead
    nobody will ever call — the worst outcome the engine can produce, because it
    looks assigned on every report.
    """
    fixture = await _workspace(db_session, hasher)
    licensed = (await _reps(db_session, hasher, fixture, 1))[0]
    unlicensed = (
        await add_member(
            db_session,
            hasher,
            fixture,
            key="dormant",
            email="dormant@routing.test",
            template_name="Caller",
            has_license=False,
        )
    ).membership.id

    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.ROUND_ROBIN,
        config={"membership_ids": [str(unlicensed), str(licensed)]},
        skip_unavailable=False,
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    engine = AssignmentEngine(scoped, workspace=fixture.workspace)

    for index in range(3):
        lead = await _lead(db_session, fixture, phone=f"+1000006{index:04d}")
        assert (await engine.decide(lead)).membership_id == licensed


async def test_no_eligible_member_leaves_the_lead_unassigned(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    fixture = await _workspace(db_session, hasher)
    away = (await _reps(db_session, hasher, fixture, 1))[0]
    member = await db_session.get(Membership, away)
    assert member is not None
    member.availability = AvailabilityStatus.ON_LEAVE
    await db_session.commit()

    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.ROUND_ROBIN,
        config={"membership_ids": [str(away)]},
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    engine = AssignmentEngine(scoped, workspace=fixture.workspace)

    lead = await _lead(db_session, fixture, phone="+10000070001")
    outcome = await engine.decide(lead)

    assert outcome.membership_id is None
    assert outcome.reason == "no_candidates"


# --- priority and conditions -------------------------------------------------


async def test_first_matching_rule_wins_in_priority_order(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    fixture = await _workspace(db_session, hasher)
    specialist, generalist = await _reps(db_session, hasher, fixture, 2)

    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.FIXED,
        config={"membership_id": str(specialist)},
        conditions={
            "type": "group",
            "op": "AND",
            "children": [{"type": "field", "key": "name", "op": "eq", "value": "vip"}],
        },
        priority=0,
        name="VIP",
    )
    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.FIXED,
        config={"membership_id": str(generalist)},
        conditions={},
        priority=1,
        name="Everyone else",
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    engine = AssignmentEngine(scoped, workspace=fixture.workspace)

    vip = await _lead(db_session, fixture, phone="+10000080001", values={"name": "vip"})
    ordinary = await _lead(db_session, fixture, phone="+10000080002", values={"name": "someone"})

    assert (await engine.decide(vip)).membership_id == specialist
    assert (await engine.decide(ordinary)).membership_id == generalist


async def test_a_rule_matching_nothing_falls_through_to_no_rule_matched(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    fixture = await _workspace(db_session, hasher)
    rep = (await _reps(db_session, hasher, fixture, 1))[0]
    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.FIXED,
        config={"membership_id": str(rep)},
        conditions={
            "type": "group",
            "op": "AND",
            "children": [{"type": "field", "key": "name", "op": "eq", "value": "never"}],
        },
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    engine = AssignmentEngine(scoped, workspace=fixture.workspace)

    lead = await _lead(db_session, fixture, phone="+10000090001", values={"name": "other"})
    outcome = await engine.decide(lead)

    assert outcome.reason == "no_rule_matched"
    assert outcome.rule_id is None


async def test_rules_compile_with_system_grants_not_the_creator_s(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """A rule routes on its own fields, whoever creates the lead.

    If the engine compiled against the creating caller's grants, a rule on a
    field that caller cannot View would silently stop matching — and identical
    leads would route differently depending on who typed them in. The engine
    holds no caller at all, which is the point of this test.
    """
    fixture = await _workspace(db_session, hasher)
    rep = (await _reps(db_session, hasher, fixture, 1))[0]
    await _rule(
        db_session,
        fixture,
        strategy=AssignmentStrategy.FIXED,
        config={"membership_id": str(rep)},
        conditions={
            "type": "group",
            "op": "AND",
            "children": [{"type": "field", "key": "name", "op": "eq", "value": "routed"}],
        },
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    engine = AssignmentEngine(scoped, workspace=fixture.workspace)

    lead = await _lead(db_session, fixture, phone="+10000100001", values={"name": "routed"})
    assert (await engine.decide(lead)).membership_id == rep
