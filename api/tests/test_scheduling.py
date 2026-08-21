"""The scheduler, scheduled reports and recurring dates (M8).

Three properties carry this milestone, and all three are invisible in a
single-timezone, single-member test — which is why each gets an explicit one:

- cron is evaluated in the **workspace's** timezone, not the server's
- a report renders as its **creator**, so field permissions govern the inbox
- a missed window is caught up **once**, not once per missed occurrence
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import add_member, build_workspace, login

from app.auth.passwords import PasswordHasherService
from app.models import Lead, ScheduledReport
from app.services.email import RecordingEmailSender
from app.services.scheduling import (
    RecurringDateService,
    ScheduledReportService,
    is_due,
    run_due_schedules,
    validate_cron,
)
from app.tenancy.session import ScopedSession

pytestmark = pytest.mark.integration


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


# --- the timezone property ---------------------------------------------------


def test_cron_fires_in_the_workspace_timezone_not_the_server_s() -> None:
    """09:00 daily means nine in the morning where the sales team sits.

    The same instant is 03:30 UTC and 09:00 in Kolkata. A scheduler that
    evaluated in its own zone would be right only for customers who happen to
    share it, and the customer would report it as "the report arrives at the
    wrong time" with no way to guess why.
    """
    cron = "0 9 * * *"
    # 03:30 UTC == 09:00 IST. Due in Kolkata, not yet due in UTC.
    now = dt.datetime(2026, 8, 21, 3, 30, tzinfo=dt.UTC)
    last = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.UTC)

    assert is_due(cron=cron, timezone="Asia/Kolkata", last_run_at=last, now=now, catchup_hours=24)
    assert not is_due(cron=cron, timezone="UTC", last_run_at=last, now=now, catchup_hours=24)


def test_a_long_outage_is_caught_up_once_not_once_per_occurrence() -> None:
    """After a four-hour gap an hourly schedule is due once, not four times."""
    cron = "0 * * * *"
    now = dt.datetime(2026, 8, 21, 12, 5, tzinfo=dt.UTC)
    last = dt.datetime(2026, 8, 21, 8, 0, tzinfo=dt.UTC)

    # Due — there were occurrences in the gap.
    assert is_due(cron=cron, timezone="UTC", last_run_at=last, now=now, catchup_hours=24)
    # And once it has run, it is not due again until the next hour.
    assert not is_due(
        cron=cron,
        timezone="UTC",
        last_run_at=now,
        now=now + dt.timedelta(minutes=1),
        catchup_hours=24,
    )


def test_a_never_run_schedule_does_not_replay_its_whole_history() -> None:
    """`last_run_at` is null on a new schedule; the catch-up floor bounds it."""
    now = dt.datetime(2026, 8, 21, 12, 0, tzinfo=dt.UTC)
    assert is_due(cron="0 * * * *", timezone="UTC", last_run_at=None, now=now, catchup_hours=24)
    # With a one-hour floor and a yearly cron, there is nothing in the window.
    assert not is_due(cron="0 0 1 1 *", timezone="UTC", last_run_at=None, now=now, catchup_hours=1)


def test_an_invalid_cron_is_refused_at_write_time() -> None:
    validate_cron("*/15 * * * *")
    with pytest.raises(Exception) as caught:
        validate_cron("not a cron")
    assert "invalid_cron" in str(caught.value)


# --- the permission property -------------------------------------------------


async def test_a_report_renders_with_its_creator_s_field_permissions(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """The one read in the product that uses somebody else's grants.

    A caller's schedule must not mail a column that caller cannot see. Getting
    this wrong is silent — the file looks fine, it just contains more than it
    should — so it is asserted directly on the rendered bytes.
    """
    fixture = await build_workspace(
        db_session, hasher, name="Reports Co", owner_email="owner@reports.example.com"
    )
    caller = await add_member(
        db_session,
        hasher,
        fixture,
        key="caller",
        email="caller@reports.example.com",
        template_name="Caller",
    )

    lead = Lead(
        workspace_id=fixture.workspace.id,
        identity_value="+19995551234",
        values={"phone": "+19995551234", "name": "Someone"},
    )
    db_session.add(lead)
    await db_session.commit()

    scoped = ScopedSession(db_session, fixture.workspace.id)
    sender = RecordingEmailSender()
    service = ScheduledReportService(scoped, workspace=fixture.workspace, sender=sender)

    owned_by_caller = await service.create(
        name="Caller's list",
        report_type="leads",
        cron="0 9 * * *",
        recipients=["ops@reports.example.com"],
        created_by=caller.membership.id,
    )
    owned_by_owner = await service.create(
        name="Owner's list",
        report_type="leads",
        cron="0 9 * * *",
        recipients=["ops@reports.example.com"],
        created_by=fixture.owner.membership.id,
    )
    await db_session.commit()

    caller_csv = (await service.render(owned_by_caller)).content.decode()
    owner_csv = (await service.render(owned_by_owner)).content.decode()

    # The Caller template holds no field grants by default, so its render has
    # only the identity column. The owner is an admin and sees everything.
    assert caller_csv.splitlines()[0] == "Identity"
    assert "Name" in owner_csv.splitlines()[0]
    assert len(owner_csv.splitlines()[0]) > len(caller_csv.splitlines()[0])


async def test_a_schedule_whose_creator_has_gone_refuses_rather_than_escalating(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """No creator means no grants to render with — and no grants is not 'all'."""
    fixture = await build_workspace(
        db_session, hasher, name="Orphan Co", owner_email="owner@orphan.example.com"
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    service = ScheduledReportService(
        scoped, workspace=fixture.workspace, sender=RecordingEmailSender()
    )
    report = await service.create(
        name="Orphaned",
        report_type="leads",
        cron="0 9 * * *",
        recipients=["ops@orphan.example.com"],
        created_by=None,
    )
    await db_session.commit()

    with pytest.raises(Exception) as caught:
        await service.render(report)
    assert "orphaned_schedule" in str(caught.value)


# --- the tick ----------------------------------------------------------------


async def test_the_tick_sends_a_due_schedule_and_skips_one_that_is_not(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    fixture = await build_workspace(
        db_session, hasher, name="Tick Co", owner_email="owner@tick.example.com"
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    sender = RecordingEmailSender()
    service = ScheduledReportService(scoped, workspace=fixture.workspace, sender=sender)

    await service.create(
        name="Every hour",
        report_type="leads",
        cron="0 * * * *",
        recipients=["ops@tick.example.com"],
        created_by=fixture.owner.membership.id,
    )
    await service.create(
        name="Once a year",
        report_type="leads",
        cron="0 0 1 1 *",
        recipients=["ops@tick.example.com"],
        created_by=fixture.owner.membership.id,
    )
    await db_session.commit()

    result = await run_due_schedules(
        scoped,
        workspace=fixture.workspace,
        sender=sender,
        now=dt.datetime(2026, 8, 21, 12, 5, tzinfo=dt.UTC),
        catchup_hours=24,
    )
    await db_session.commit()

    assert result.considered == 2
    assert result.sent == 1
    assert result.skipped == 1
    assert len(sender.sent) == 1
    assert sender.sent[0].to == ("ops@tick.example.com",)
    assert sender.sent[0].attachments[0].filename == "every-hour.csv"


async def test_one_failing_schedule_does_not_stop_the_others(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """A broken row records its error and the tick carries on."""
    fixture = await build_workspace(
        db_session, hasher, name="Resilient Co", owner_email="owner@resilient.example.com"
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    sender = RecordingEmailSender()
    service = ScheduledReportService(scoped, workspace=fixture.workspace, sender=sender)

    broken = await service.create(
        name="Broken",
        report_type="leads",
        cron="0 * * * *",
        recipients=["ops@resilient.example.com"],
        created_by=None,  # no creator -> render refuses
    )
    await service.create(
        name="Fine",
        report_type="leads",
        cron="0 * * * *",
        recipients=["ops@resilient.example.com"],
        created_by=fixture.owner.membership.id,
    )
    await db_session.commit()

    result = await run_due_schedules(
        scoped,
        workspace=fixture.workspace,
        sender=sender,
        now=dt.datetime(2026, 8, 21, 12, 5, tzinfo=dt.UTC),
        catchup_hours=24,
    )
    await db_session.commit()

    assert result.sent == 1
    assert result.failed == 1
    reloaded = await db_session.get(ScheduledReport, broken.id)
    assert reloaded is not None
    assert reloaded.last_error is not None, "a broken schedule must say so in settings"


# --- recurring dates ---------------------------------------------------------


async def test_recurring_date_occurrences_land_in_the_requested_window(
    db_session: AsyncSession, hasher: PasswordHasherService, api: AsyncClient
) -> None:
    """The greeting scheduler's input, and a manager's birthday list.

    The stored `next` goes stale because it is relative to the day it was
    written, so this recomputes on read — which is what the registry's own note
    asks for.
    """
    fixture = await build_workspace(
        db_session, hasher, name="Dates Co", owner_email="owner@dates.example.com"
    )
    await login(api, fixture.owner)

    created = await api.post(
        fixture.path("/settings/lead-fields"),
        headers=fixture.owner.auth,
        json={"label": "Anniversary", "field_type": "RECURRING_DATE"},
    )
    assert created.status_code == 201, created.text
    key = created.json()["key"]

    lead = await api.post(
        fixture.path("/leads"),
        headers=fixture.owner.auth,
        json={
            "values": {
                "name": "Celebrant",
                "phone": "+19995559876",
                key: {"start": "2020-03-14", "frequency": "YEARLY", "interval": 1},
            }
        },
    )
    assert lead.status_code == 201, lead.text

    scoped = ScopedSession(db_session, fixture.workspace.id)
    rows = await RecurringDateService(scoped).occurrences(
        field_key=key, start=dt.date(2027, 1, 1), end=dt.date(2027, 12, 31)
    )
    assert [o.occurs_on for o in rows] == [dt.date(2027, 3, 14)]

    # And nothing in a window the anniversary does not fall in.
    empty = await RecurringDateService(scoped).occurrences(
        field_key=key, start=dt.date(2027, 5, 1), end=dt.date(2027, 6, 1)
    )
    assert empty == []


async def test_occurrences_refuses_a_field_that_is_not_a_recurring_date(
    db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    fixture = await build_workspace(
        db_session, hasher, name="Wrong Field Co", owner_email="owner@wrong.example.com"
    )
    scoped = ScopedSession(db_session, fixture.workspace.id)
    with pytest.raises(Exception) as caught:
        await RecurringDateService(scoped).occurrences(
            field_key="name", start=dt.date(2026, 1, 1), end=dt.date(2026, 12, 31)
        )
    assert "not_a_recurring_date" in str(caught.value)


def test_the_zone_used_is_the_workspace_s_even_across_a_dst_boundary() -> None:
    """A 09:00 schedule stays 09:00 local when the clocks change.

    Hand-rolled cron matching is where this breaks: the UTC offset moves, so a
    fixed-offset implementation drifts by an hour twice a year and nobody
    connects the report's new arrival time to the clock change.
    """
    zone = ZoneInfo("America/New_York")
    cron = "0 9 * * *"

    # 2026-11-01 is the US fall-back. 09:00 EST that day is 14:00 UTC.
    after = dt.datetime(2026, 11, 1, 14, 0, tzinfo=dt.UTC)
    assert after.astimezone(zone).hour == 9
    assert is_due(
        cron=cron,
        timezone="America/New_York",
        last_run_at=dt.datetime(2026, 10, 31, 14, 0, tzinfo=dt.UTC),
        now=after,
        catchup_hours=24,
    )
