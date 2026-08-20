"""Builds the fictional demo workspace.

Two halves, deliberately built by different means:

**Configuration** goes through the real services — `FieldService`,
`PipelineService`, `CustomActionService`, `WorkspaceProvisioner`. It is a few
dozen rows, and routing it through the engine means the seed exercises the same
validation a customer's admin would hit. A seed that wrote configuration
straight to the tables could produce a workspace the product itself would refuse
to create, which would make every test built on it a lie.

**Leads and actions** go through `COPY`. There are 50,000 of them plus a
log-normal number of actions each; the ORM would take a quarter of an hour, and
the point of this fixture is to exist in under three minutes.

The generated history is *causally coherent*: a lead is created, then contacted,
then progresses down the funnel or is lost, and the stage it currently sits in
is the one its last `STAGE_CHANGE` moved it to. Random current states unrelated
to their own timelines would let every history-filter test pass for the wrong
reason, which is worse than having no fixture at all.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import random
import time
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.fields.search import SEARCH_CONFIG
from app.models.enums import ChangesetSource, StageKind, SystemActionKind
from app.models.field import CustomActionType, FieldOption, LeadField
from app.models.lead import Changeset
from app.models.permission import TemplateFieldGrant
from app.models.pipeline import CallDisposition, LostReason, Stage
from app.models.user import User
from app.models.workspace import Membership, PermissionTemplate, Workspace
from app.seed.fixture import (
    BRANCH_BATCH_PARENTS,
    CUSTOM_ACTIONS,
    LEAD_FIELDS,
    LOST_REASONS,
    MEMBERS,
    STAGES,
    TEMPLATE_GRANTS,
    WORKSPACE_NAME,
)
from app.services.fields import FieldService
from app.services.pipeline import CustomActionService, PipelineService
from app.services.provisioning import WorkspaceProvisioner
from app.tenancy.session import ScopedSession

__all__ = ["DemoSeeder", "SeedResult"]

#: Rows per COPY. Large enough that round trips disappear, small enough that an
#: encoded batch stays a sensible size in memory.
_BATCH = 5_000

#: Roughly a year of history, so a "last 7 days" window has data on both sides.
_HISTORY_DAYS = 365

#: §8: "~30% sparse values". A fixture where every field is populated hides
#: every null-handling bug in the product.
_SPARSITY = 0.30

#: Probability of surviving each funnel step. Compounding these is what gives
#: the stage distribution its shape — most leads never leave the early stages,
#: which is what a real pipeline looks like and what makes a report worth
#: drawing.
_ADVANCE = 0.55

#: Of the leads that stop advancing, how many are explicitly marked lost rather
#: than simply going quiet where they are. Most stalled leads are never
#: dispositioned at all — that is exactly the population the
#: "no outgoing call in 14 days" filter exists to surface, so the fixture must
#: not tidily mark them Lost.
_LOST_IF_STALLED = 0.22

_FIRST_NAMES = [
    "Aarav",
    "Ananya",
    "Rohan",
    "Meera",
    "Kabir",
    "Ishita",
    "Vihaan",
    "Diya",
    "Arjun",
    "Saanvi",
    "Reyansh",
    "Aisha",
    "Vivaan",
    "Kiara",
    "Aditya",
    "Myra",
    "Krishna",
    "Anika",
    "Devansh",
    "Navya",
    "Farhan",
    "Zoya",
    "Imran",
    "Rhea",
    "Joel",
    "Tara",
    "Nikhil",
    "Leela",
    "Omar",
    "Sneha",
]
_LAST_NAMES = [
    "Sharma",
    "Iyer",
    "Fernandes",
    "Menon",
    "Rao",
    "Sheikh",
    "Banerjee",
    "Kulkarni",
    "Nair",
    "Pillai",
    "Das",
    "Bose",
    "Chowdhury",
    "Reddy",
    "Kapoor",
    "Malhotra",
    "Joshi",
    "Mehta",
    "Verma",
    "Gupta",
]
_AREAS = ("Adyar", "Velachery", "Anna Nagar", "T Nagar", "Mylapore", "Guindy")

_LEAD_COLUMNS = (
    "id",
    "workspace_id",
    "identity_value",
    "values",
    "stage_id",
    "lost_reason_id",
    "assignee_id",
    "rating",
    "score",
    "last_action_at",
    "created_at",
    "updated_at",
    "created_by_id",
    "deleted_at",
)

_ACTION_COLUMNS = (
    "id",
    "workspace_id",
    "lead_id",
    "changeset_id",
    "kind",
    "action_type_id",
    "actor_id",
    "payload",
    "body",
    "score_applied",
    "is_pinned",
    "performed_at",
    "created_at",
    "updated_at",
)


@dataclasses.dataclass(frozen=True, slots=True)
class SeedResult:
    """What the run produced, for the CLI to print and tests to assert on."""

    workspace_id: uuid.UUID
    workspace_slug: str
    leads: int
    actions: int
    seconds: float


class DemoSeeder:
    """Generates the Northwind Tutors workspace.

    Takes an engine as well as a session because the bulk half needs the raw
    asyncpg connection underneath SQLAlchemy in order to run `COPY`.
    """

    def __init__(
        self,
        session: AsyncSession,
        engine: AsyncEngine,
        *,
        lead_count: int = 50_000,
        seed: int = 42,
    ) -> None:
        self._session = session
        self._engine = engine
        self._lead_count = lead_count
        # Seeded explicitly so two runs produce the same workspace: a
        # performance number that moved because the data moved tells you
        # nothing about the code.
        self._random = random.Random(seed)
        self._now = dt.datetime.now(dt.UTC)

    # --- entry point -------------------------------------------------------

    async def run(self) -> SeedResult:
        started = time.monotonic()

        owner = await self._create_user(MEMBERS[0])
        workspace, owner_membership = await WorkspaceProvisioner(self._session).provision(
            name=WORKSPACE_NAME,
            owner=owner,
            seat_limit=len(MEMBERS),
        )
        scoped = ScopedSession(self._session, workspace.id)

        fields = await self._configure_fields(scoped)
        await self._configure_pipeline(scoped)
        action_types = await self._configure_actions(scoped)
        memberships = await self._add_members(workspace, owner_membership)
        await self._apply_template_grants(workspace, fields)
        await self._session.commit()

        stages, lost_reasons, dispositions, options = await self._load_taxonomy(scoped)

        changeset = Changeset(
            workspace_id=workspace.id,
            source=ChangesetSource.IMPORT,
            actor_id=owner_membership.id,
            summary=f"Seeded {self._lead_count} demo leads",
            lead_count=self._lead_count,
        )
        self._session.add(changeset)
        await self._session.commit()

        actions = await self._bulk_generate(
            workspace=workspace,
            changeset_id=changeset.id,
            fields=fields,
            options=options,
            stages=stages,
            lost_reasons=lost_reasons,
            dispositions=dispositions,
            action_types=action_types,
            memberships=memberships,
        )
        await self._build_search_vectors(workspace.id, fields)

        return SeedResult(
            workspace_id=workspace.id,
            workspace_slug=workspace.slug,
            leads=self._lead_count,
            actions=actions,
            seconds=round(time.monotonic() - started, 1),
        )

    # --- configuration -----------------------------------------------------

    async def _create_user(self, member: tuple[str, str, str, str]) -> User:
        _, full_name, email, _ = member
        user = User(
            email=email,
            full_name=full_name,
            # Deliberately not a usable credential. A seeded login would be a
            # shipped default password, and this workspace is for reading.
            password_hash="!seeded-no-login",
            is_active=True,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def _configure_fields(self, scoped: ScopedSession) -> list[LeadField]:
        """One field of every type, with options for the choice-based ones."""
        service = FieldService(scoped)
        created: list[LeadField] = []

        for label, field_type, options in LEAD_FIELDS:
            field = await service.create_field(label=label, field_type=field_type)
            created.append(field)

            by_label: dict[str, FieldOption] = {}
            for option_label in options:
                parent_label = BRANCH_BATCH_PARENTS.get(option_label)
                by_label[option_label] = await service.add_option(
                    field.id,
                    label=option_label,
                    parent_option_id=by_label[parent_label].id if parent_label else None,
                )

        # Sorting is restricted to indexed fields, so the fixture declares the
        # two a demo actually sorts by. These land PENDING; the worker builds
        # them when an API process is running.
        for field in created:
            if field.key in {"quoted_fee", "enrolment_date"}:
                await service.declare_indexed(field.id)

        await self._session.flush()
        return created

    async def _configure_pipeline(self, scoped: ScopedSession) -> None:
        service = PipelineService(scoped)
        for label in STAGES:
            await service.create_stage(label=label)
        for label in LOST_REASONS:
            await service.create_lost_reason(label=label)
        await self._session.flush()

    async def _configure_actions(self, scoped: ScopedSession) -> list[CustomActionType]:
        service = CustomActionService(scoped)
        created: list[CustomActionType] = []
        for name, direction, score, action_fields in CUSTOM_ACTIONS:
            action_type = await service.create_type(name=name, direction=direction, score=score)
            for label, field_type in action_fields:
                await service.add_field(action_type.id, label=label, field_type=field_type)
            created.append(action_type)
        await self._session.flush()
        return created

    async def _templates_by_name(self, workspace: Workspace) -> dict[str, PermissionTemplate]:
        rows = await self._session.execute(
            select(PermissionTemplate).where(PermissionTemplate.workspace_id == workspace.id)
        )
        return {t.name: t for t in rows.scalars().all()}

    async def _add_members(
        self, workspace: Workspace, owner_membership: Membership
    ) -> list[Membership]:
        """Five people across the five templates, in a reporting line.

        The manager has the two callers reporting to them, so the M1 visibility
        rules have something to resolve and a manager's lead list is genuinely
        narrower than an admin's.
        """
        templates = await self._templates_by_name(workspace)
        memberships = [owner_membership]
        manager: Membership | None = None

        for member in MEMBERS[1:]:
            _, _, _, template_name = member
            user = await self._create_user(member)
            membership = Membership(
                workspace_id=workspace.id,
                user_id=user.id,
                template_id=templates[template_name].id,
                has_license=True,
                manager_id=manager.id if template_name == "Caller" and manager else None,
            )
            self._session.add(membership)
            await self._session.flush()
            if template_name == "Manager":
                manager = membership
            memberships.append(membership)

        return memberships

    async def _apply_template_grants(
        self, workspace: Workspace, fields: Sequence[LeadField]
    ) -> None:
        """Give three templates genuinely different matrices.

        §8 asks for at least one field that is View-but-not-Edit and at least
        one template whose Export is empty. Both are in
        `fixture.TEMPLATE_GRANTS` — they are what make a permission test
        meaningful rather than vacuous.
        """
        by_key = {f.key: f for f in fields}
        templates = await self._templates_by_name(workspace)
        grant_names = {"view": "VIEW", "edit": "EDIT", "export": "EXPORT"}

        for template_name, grants in TEMPLATE_GRANTS.items():
            template = templates.get(template_name)
            if template is None:  # pragma: no cover - provisioning guarantees these
                continue
            for grant_key, keys in grants.items():
                for key in keys:
                    field = by_key.get(key)
                    if field is None:  # pragma: no cover - keys come from the fixture
                        continue
                    self._session.add(
                        TemplateFieldGrant(
                            workspace_id=workspace.id,
                            template_id=template.id,
                            field_id=field.id,
                            grant=grant_names[grant_key],
                        )
                    )
        await self._session.flush()

    async def _load_taxonomy(
        self, scoped: ScopedSession
    ) -> tuple[
        list[Stage], list[LostReason], list[CallDisposition], dict[uuid.UUID, list[FieldOption]]
    ]:
        stages = list((await scoped.execute(scoped.select(Stage))).scalars().all())
        stages.sort(key=lambda s: s.sort_order)
        reasons = list((await scoped.execute(scoped.select(LostReason))).scalars().all())
        dispositions = list((await scoped.execute(scoped.select(CallDisposition))).scalars().all())
        options: dict[uuid.UUID, list[FieldOption]] = {}
        for option in (await scoped.execute(scoped.select(FieldOption))).scalars().all():
            options.setdefault(option.field_id, []).append(option)
        return stages, reasons, dispositions, options

    # --- generation --------------------------------------------------------

    def _maybe(self, value: Any) -> Any:
        """Leave a field empty ~30% of the time (§8)."""
        return None if self._random.random() < _SPARSITY else value

    def _values_for(
        self,
        index: int,
        fields: Sequence[LeadField],
        options: dict[uuid.UUID, list[FieldOption]],
        phone: str,
    ) -> dict[str, Any]:
        """One lead's JSONB blob, in the exact shapes the registry normalises to.

        Written to match `app.fields.registry` rather than invented: a fixture
        whose composites had the wrong sub-keys would make the filter compiler
        look broken when it was reading exactly what it was told to.
        """
        rng = self._random
        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)
        values: dict[str, Any] = {"name": f"{first} {last}", "phone": phone}

        for field in fields:
            key = field.key
            choices = options.get(field.id, [])
            match field.field_type.value:
                case "TEXT":
                    values[key] = self._maybe(f"{rng.choice(_FIRST_NAMES)} {last}")
                case "DROPDOWN":
                    values[key] = self._maybe(rng.choice(choices).code) if choices else None
                case "TAGS":
                    if choices:
                        picked = rng.sample(choices, k=rng.randint(1, min(3, len(choices))))
                        values[key] = self._maybe([o.code for o in picked])
                case "EMAIL":
                    values[key] = self._maybe(f"{first.lower()}.{last.lower()}@example.com")
                case "PHONE":
                    values[key] = self._maybe(f"+9198{rng.randint(0, 99_999_999):08d}")
                case "CHECKBOX":
                    values[key] = self._maybe(rng.random() < 0.3)
                case "DATE":
                    day = self._now.date() - dt.timedelta(days=rng.randint(-90, _HISTORY_DAYS))
                    values[key] = self._maybe(day.isoformat())
                case "MONEY":
                    amount = rng.randrange(5_000, 120_000, 500)
                    values[key] = self._maybe({"amount": f"{amount}.00", "currency": "INR"})
                case "NUMBER":
                    values[key] = self._maybe(rng.randint(6, 12))
                case "WEBSITE":
                    values[key] = self._maybe("https://example.com/enquiry")
                case "DEPENDENT_DROPDOWN":
                    children = [o for o in choices if o.parent_option_id is not None]
                    if children:
                        child = rng.choice(children)
                        parent = next((o for o in choices if o.id == child.parent_option_id), None)
                        values[key] = self._maybe(
                            {"value": child.code, "parent": parent.code if parent else None}
                        )
                case "RECURRING_DATE":
                    start = self._now.date() - dt.timedelta(days=rng.randint(0, 300))
                    nxt = self._now.date() + dt.timedelta(days=rng.randint(0, 60))
                    values[key] = self._maybe(
                        {
                            "start": start.isoformat(),
                            "frequency": "MONTHLY",
                            "interval": 1,
                            "next": nxt.isoformat(),
                        }
                    )
                case "LOCATION":
                    values[key] = self._maybe(
                        {"city": "Chennai", "state": "Tamil Nadu", "line1": rng.choice(_AREAS)}
                    )

        return {k: v for k, v in values.items() if v is not None}

    def _timeline(
        self,
        *,
        created_at: dt.datetime,
        stages: Sequence[Stage],
        dispositions: Sequence[CallDisposition],
        action_types: Sequence[CustomActionType],
        assignee_id: uuid.UUID | None,
    ) -> tuple[list[dict[str, Any]], Stage, bool]:
        """One lead's history, and where it left the lead.

        Walks the funnel forward in time. Every stage change is preceded by the
        contact that plausibly caused it, so `action_not_performed` over this
        data means what it says.
        """
        rng = self._random
        active = [s for s in stages if s.kind is StageKind.ACTIVE]
        initial = next(s for s in stages if s.kind is StageKind.INITIAL)
        won = next(s for s in stages if s.kind is StageKind.WON)
        lost = next(s for s in stages if s.kind is StageKind.LOST)

        timeline: list[dict[str, Any]] = [
            {"kind": SystemActionKind.LEAD_CREATED, "at": created_at, "payload": {}, "score": 0}
        ]
        at = created_at
        current = initial
        went_lost = False

        def step(minimum: int, maximum: int) -> dt.datetime:
            nonlocal at
            at = at + dt.timedelta(hours=rng.randint(minimum, maximum))
            return min(at, self._now)

        for stage in active:
            if rng.random() > _ADVANCE:
                break
            # The call that caused the move.
            timeline.append(
                {
                    "kind": SystemActionKind.CALL_LOGGED,
                    "at": step(2, 96),
                    "payload": {
                        "direction": "OUTGOING",
                        "disposition_id": str(rng.choice(dispositions).id),
                        "duration_seconds": rng.randint(15, 600),
                        "notes": None,
                    },
                    "score": 0,
                }
            )
            timeline.append(
                {
                    "kind": SystemActionKind.STAGE_CHANGE,
                    "at": step(1, 12),
                    "payload": {
                        "old_stage_id": str(current.id),
                        "new_stage_id": str(stage.id),
                        "lost_reason_id": None,
                    },
                    "score": 0,
                }
            )
            current = stage

            if action_types and rng.random() < 0.45:
                chosen = rng.choice(action_types)
                timeline.append(
                    {
                        "kind": SystemActionKind.CUSTOM,
                        "at": step(1, 72),
                        "payload": {},
                        "score": chosen.score,
                        "action_type_id": chosen.id,
                    }
                )

        # Where the walk ends: won if it reached the end of the funnel, lost if
        # it stalled and someone said so, otherwise it simply sits where it is.
        reached_end = current is active[-1] if active else False
        if reached_end and rng.random() < _ADVANCE:
            final = won
        elif rng.random() < _LOST_IF_STALLED:
            final = lost
            went_lost = True
        else:
            final = current

        if final is not current:
            timeline.append(
                {
                    "kind": SystemActionKind.STAGE_CHANGE,
                    "at": step(1, 240),
                    "payload": {
                        "old_stage_id": str(current.id),
                        "new_stage_id": str(final.id),
                        "lost_reason_id": None,
                    },
                    "score": 0,
                }
            )

        if assignee_id is not None:
            timeline.insert(
                1,
                {
                    "kind": SystemActionKind.ASSIGNMENT_CHANGE,
                    "at": created_at + dt.timedelta(minutes=rng.randint(1, 240)),
                    "payload": {"old_assignee_id": None, "new_assignee_id": str(assignee_id)},
                    "score": 0,
                },
            )

        timeline.sort(key=lambda a: a["at"])
        return timeline, final, went_lost

    async def _bulk_generate(
        self,
        *,
        workspace: Workspace,
        changeset_id: uuid.UUID,
        fields: Sequence[LeadField],
        options: dict[uuid.UUID, list[FieldOption]],
        stages: Sequence[Stage],
        lost_reasons: Sequence[LostReason],
        dispositions: Sequence[CallDisposition],
        action_types: Sequence[CustomActionType],
        memberships: Sequence[Membership],
    ) -> int:
        rng = self._random
        # Only the callers carry pipeline, which is what a real workspace looks
        # like and what makes the manager-sees-their-reports rule visible.
        assignable = [m.id for m in memberships[-2:]]
        custom_fields = [f for f in fields if not f.is_builtin]

        lead_rows: list[tuple[Any, ...]] = []
        action_rows: list[tuple[Any, ...]] = []
        total_actions = 0

        for index in range(self._lead_count):
            lead_id = uuid.uuid4()
            phone = f"+9199{index:08d}"
            created_at = self._now - dt.timedelta(
                days=rng.randint(0, _HISTORY_DAYS), minutes=rng.randint(0, 1440)
            )
            assignee_id = rng.choice(assignable) if rng.random() < 0.85 else None

            timeline, final_stage, went_lost = self._timeline(
                created_at=created_at,
                stages=stages,
                dispositions=dispositions,
                action_types=action_types,
                assignee_id=assignee_id,
            )

            score = sum(int(a["score"]) for a in timeline)
            last_at = max(a["at"] for a in timeline)
            values = self._values_for(index, custom_fields, options, phone)

            lead_rows.append(
                (
                    lead_id,
                    workspace.id,
                    phone,
                    json.dumps(values),
                    final_stage.id,
                    rng.choice(lost_reasons).id if went_lost and lost_reasons else None,
                    assignee_id,
                    rng.randint(1, 5) if rng.random() < 0.4 else None,
                    score,
                    last_at,
                    created_at,
                    last_at,
                    memberships[0].id,
                    None,
                )
            )

            for action in timeline:
                action_rows.append(
                    (
                        uuid.uuid4(),
                        workspace.id,
                        lead_id,
                        changeset_id,
                        action["kind"].value,
                        action.get("action_type_id"),
                        assignee_id,
                        json.dumps(action["payload"]),
                        None,
                        int(action["score"]),
                        False,
                        action["at"],
                        action["at"],
                        action["at"],
                    )
                )
            total_actions += len(timeline)

            if len(lead_rows) >= _BATCH:
                await self._copy(lead_rows, action_rows)
                lead_rows, action_rows = [], []

        if lead_rows:
            await self._copy(lead_rows, action_rows)
        return total_actions

    async def _copy(
        self, lead_rows: Sequence[tuple[Any, ...]], action_rows: Sequence[tuple[Any, ...]]
    ) -> None:
        """Push one batch through `COPY`.

        Reaches past SQLAlchemy to the asyncpg connection: `COPY` is a protocol
        feature, not a statement, so there is no ORM equivalent. Leads go first
        — actions carry a foreign key to them.
        """
        async with self._engine.begin() as connection:
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection
            assert driver is not None, "COPY needs the asyncpg connection"
            await driver.copy_records_to_table(
                "leads", records=lead_rows, columns=list(_LEAD_COLUMNS)
            )
            if action_rows:
                await driver.copy_records_to_table(
                    "actions", records=action_rows, columns=list(_ACTION_COLUMNS)
                )

    async def _build_search_vectors(
        self, workspace_id: uuid.UUID, fields: Sequence[LeadField]
    ) -> None:
        """Populate `search_vector` for every seeded lead, in one statement.

        The write paths maintain this per lead; `COPY` bypasses them, so it is
        rebuilt here from the same searchable keys `app.fields.search` would
        have used. Doing it in SQL keeps 50,000 tsvectors out of Python.
        """
        from app.fields.search import searchable_keys

        keys = searchable_keys(fields)
        # Keys are slugs from our own table, and the same `_SAFE_KEY` shape the
        # filter compiler validates before interpolating.
        parts = ["identity_value"] + [f"coalesce(values ->> '{key}', '')" for key in keys]
        statement = text(
            f"UPDATE leads SET search_vector = to_tsvector("
            f"'{SEARCH_CONFIG}', concat_ws(' ', {', '.join(parts)})) "
            f"WHERE workspace_id = :workspace_id"
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement, {"workspace_id": workspace_id})
