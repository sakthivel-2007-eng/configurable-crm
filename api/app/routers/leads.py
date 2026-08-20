"""Lead, action and message-template endpoints (M5).

`docs/02-api-contract.md` §Leads and §Actions.

Every handler here obtains its `LeadService` from `_lead_service`, which binds
the caller's projection and write filter before the service exists. There is no
constructor path that produces an unfiltered service, which is what makes
"every read goes through projection" a property of the code rather than a rule
people remember.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.errors import api_error, not_found
from app.models.enums import ChangesetSource, SystemActionKind, TemplateChannel
from app.models.field import CustomActionType
from app.models.lead import Changeset
from app.models.pipeline import CallDisposition
from app.schemas.common import Page
from app.schemas.filter import LeadSearchRequest
from app.schemas.lead import (
    ActionRead,
    CallLogCreate,
    ChangesetRead,
    CustomActionLog,
    LeadCreate,
    LeadUpdate,
    MessageTemplateCreate,
    NoteCreate,
    TemplateRenderRequest,
)
from app.services.leads import LeadService
from app.services.templates import MessageTemplateService
from app.tenancy.scoping import WorkspaceScope, require_workspace

router = APIRouter(tags=["leads"])


async def _lead_service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> LeadService:
    """Build the service with this caller's chokepoints already bound."""
    return LeadService(
        scope.session,
        workspace=scope.workspace,
        projection=await scope.projection(),
        write_filter=await scope.write_filter(),
        actor_id=scope.membership_id,
        visible_membership_ids=scope.visible_membership_ids,
        sees_all=scope.sees_all_members,
    )


def _require(scope: WorkspaceScope, capability: str) -> None:
    """Capability check, in the router because it is an HTTP concern.

    The *data* rules (which fields, which leads) live in the services; this is
    only "may this caller use this endpoint at all".
    """
    if not scope.capability("leads", capability):
        raise api_error(
            403,
            "insufficient_permissions",
            f"This permission template does not allow: {capability.replace('_', ' ')}",
        )


# --- leads -------------------------------------------------------------------


@router.get("/leads", summary="List leads (never returns actions)")
async def list_leads(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LeadService, Depends(_lead_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Annotated[str, Query(max_length=80)] = "-created_at",
    columns: Annotated[list[str] | None, Query()] = None,
) -> Page[dict[str, Any]]:
    """Architecture rule 6: the list endpoint never returns actions.

    The quick path: search and sort, no filter document. Anything structured
    goes to `POST /leads/search`, which speaks the same DSL a saved filter does.
    """
    leads, total = await service.search_leads(limit=limit, offset=offset, search=q, sort=sort)
    return Page(
        items=[await service.project(lead, columns=columns) for lead in leads],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/leads/search", summary="List leads matching a filter DSL document")
async def search_leads(
    payload: LeadSearchRequest,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LeadService, Depends(_lead_service)],
) -> Page[dict[str, Any]]:
    """The full filter DSL, including the four history predicates.

    Declared before `/leads/{lead_id}`: FastAPI matches in declaration order,
    so a literal segment after a uuid placeholder would be parsed as an id and
    422 (conventions §5 — this has bitten twice already).
    """
    clause = await service.compile_filter(payload.filter)
    leads, total = await service.search_leads(
        limit=payload.limit,
        offset=payload.offset,
        clause=clause,
        search=payload.q,
        sort=payload.sort,
    )
    return Page(
        items=[await service.project(lead, columns=payload.columns) for lead in leads],
        total=total,
        limit=payload.limit,
        offset=payload.offset,
    )


@router.post("/leads", status_code=status.HTTP_201_CREATED, summary="Create a lead")
async def create_lead(
    payload: LeadCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LeadService, Depends(_lead_service)],
) -> dict[str, Any]:
    _require(scope, "manually_add_lead")
    lead, _ = await service.create_lead(
        values=payload.values,
        stage_id=payload.stage_id,
        assignee_id=payload.assignee_id,
        rating=payload.rating,
    )
    # One commit: the lead, its changeset and its actions land together.
    await scope.session.commit()
    return await service.project(lead)


@router.get("/leads/{lead_id}", summary="One lead, View-projected")
async def get_lead(
    lead_id: uuid.UUID,
    service: Annotated[LeadService, Depends(_lead_service)],
) -> dict[str, Any]:
    return await service.project(await service.get_lead(lead_id))


@router.patch("/leads/{lead_id}", summary="Update a lead")
async def update_lead(
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LeadService, Depends(_lead_service)],
) -> dict[str, Any]:
    """Writes one `FIELD_CHANGE` per changed field, all sharing one changeset."""
    _require(scope, "add_or_update")
    lead, _ = await service.update_lead(
        lead_id,
        values=payload.values,
        stage_id=payload.stage_id,
        lost_reason_id=payload.lost_reason_id,
        assignee_id=payload.assignee_id,
        rating=payload.rating,
        unset=frozenset(payload.unset or ()),
    )
    await scope.session.commit()
    return await service.project(lead)


@router.delete(
    "/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Soft delete a lead"
)
async def delete_lead(
    lead_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LeadService, Depends(_lead_service)],
) -> None:
    _require(scope, "add_or_update")
    await service.soft_delete(lead_id)
    await scope.session.commit()


# --- actions -----------------------------------------------------------------


@router.get("/leads/{lead_id}/actions", summary="A lead's timeline")
async def list_actions(
    lead_id: uuid.UUID,
    service: Annotated[LeadService, Depends(_lead_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ActionRead]:
    actions = await service.list_actions(lead_id, limit=limit, offset=offset)
    return Page(
        items=[ActionRead.model_validate(a) for a in actions],
        total=len(actions),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/leads/{lead_id}/notes",
    status_code=status.HTTP_201_CREATED,
    summary="Add a note to the timeline",
)
async def add_note(
    lead_id: uuid.UUID,
    payload: NoteCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LeadService, Depends(_lead_service)],
) -> ActionRead:
    _require(scope, "actions")
    from app.services.actions import ActionWriter

    lead = await service.get_lead(lead_id)
    writer = ActionWriter(scope.session, actor_id=scope.membership_id)
    await writer.open_changeset(
        source=ChangesetSource.SINGLE_EDIT,
        summary=f"Note on {lead.identity_value}",
    )
    action = writer.record_note(lead, body=payload.body)
    await scope.session.commit()
    return ActionRead.model_validate(action)


@router.post(
    "/leads/{lead_id}/calls",
    status_code=status.HTTP_201_CREATED,
    summary="Log a call manually (no telephony in v1)",
)
async def log_call(
    lead_id: uuid.UUID,
    payload: CallLogCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LeadService, Depends(_lead_service)],
) -> ActionRead:
    """The disposition comes from the workspace's own list (M3).

    Nothing here talks to a provider: v1 has no dialer, and this records what a
    human says happened.
    """
    _require(scope, "actions")
    from app.services.actions import ActionWriter

    lead = await service.get_lead(lead_id)
    disposition = await scope.session.get(CallDisposition, payload.disposition_id)
    if disposition is None or disposition.is_archived:
        raise not_found("Call disposition")

    writer = ActionWriter(scope.session, actor_id=scope.membership_id)
    await writer.open_changeset(
        source=ChangesetSource.SINGLE_EDIT,
        summary=f"Call logged on {lead.identity_value}",
    )
    action = writer.record_call(
        lead,
        direction=payload.direction,
        disposition_id=disposition.id,
        duration_seconds=payload.duration_seconds,
        notes=payload.notes,
    )
    await scope.session.commit()
    return ActionRead.model_validate(action)


@router.post(
    "/leads/{lead_id}/custom-actions",
    status_code=status.HTTP_201_CREATED,
    summary="Log a custom action against its dynamic form",
)
async def log_custom_action(
    lead_id: uuid.UUID,
    payload: CustomActionLog,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LeadService, Depends(_lead_service)],
) -> ActionRead:
    """Values are validated against `action_fields` through the M2 registry.

    `score_applied` is snapshotted from the type, so editing its score later
    does not rewrite this action.
    """
    _require(scope, "actions")
    from app.services.actions import ActionWriter
    from app.services.templates import validate_action_values

    lead = await service.get_lead(lead_id)
    action_type = await scope.session.get(CustomActionType, payload.action_type_id)
    if action_type is None or action_type.is_archived:
        raise not_found("Custom action")

    values = await validate_action_values(scope, action_type=action_type, values=payload.values)

    writer = ActionWriter(scope.session, actor_id=scope.membership_id)
    await writer.open_changeset(
        source=ChangesetSource.SINGLE_EDIT,
        summary=f"{action_type.name} on {lead.identity_value}",
    )
    action = await writer.record_custom(
        lead,
        action_type=action_type,
        values=values,
        performed_at=payload.performed_at,
    )
    await scope.session.commit()
    return ActionRead.model_validate(action)


# --- changesets --------------------------------------------------------------


@router.get("/changesets", summary="The edit report")
async def list_changesets(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ChangesetRead]:
    """Every mutation batch, newest first.

    M7 adds preview-undo and undo on top; the record they operate on is written
    from M5 onward, which is the point of building changesets now.
    """
    rows, total = await scope.session.list(
        Changeset, limit=limit, offset=offset, order_by=Changeset.created_at.desc()
    )
    return Page(
        items=[ChangesetRead.model_validate(c) for c in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# --- message templates -------------------------------------------------------


@router.get("/templates", summary="Message templates visible to the caller")
async def list_templates(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    channel: Annotated[TemplateChannel | None, Query()] = None,
) -> list[dict[str, Any]]:
    service = MessageTemplateService(scope.session, scope=scope)
    return [t.as_payload() for t in await service.visible(channel=channel)]


@router.post("/templates", status_code=status.HTTP_201_CREATED, summary="Create a message template")
async def create_template(
    payload: MessageTemplateCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> dict[str, Any]:
    service = MessageTemplateService(scope.session, scope=scope)
    template = await service.create(
        channel=payload.channel,
        name=payload.name,
        body=payload.body,
        subject=payload.subject,
        shared=payload.shared,
        role_template_id=payload.role_template_id,
    )
    await scope.session.commit()
    return service.payload(template)


@router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a message template",
)
async def archive_template(
    template_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> None:
    service = MessageTemplateService(scope.session, scope=scope)
    await service.archive(template_id)
    await scope.session.commit()


@router.post(
    "/templates/{template_id}/render",
    summary="Render a template against a lead, through FieldProjectionService",
)
async def render_template(
    template_id: uuid.UUID,
    payload: TemplateRenderRequest,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LeadService, Depends(_lead_service)],
) -> dict[str, Any]:
    """Substitution runs through the projection service.

    That is the whole security property: a template naming `{{salary}}` cannot
    be used to read a field the sender lacks View on — the placeholder simply
    goes unresolved and is reported.
    """
    templates = MessageTemplateService(scope.session, scope=scope)
    template = await templates.get(template_id)
    lead = await service.get_lead(payload.lead_id)
    projected = await service.project(lead)

    return templates.render(template, values=projected["values"])


@router.post(
    "/leads/{lead_id}/messages",
    status_code=status.HTTP_201_CREATED,
    summary="Record a WhatsApp/SMS/email send",
)
async def record_message(
    lead_id: uuid.UUID,
    payload: dict[str, Any],
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LeadService, Depends(_lead_service)],
) -> ActionRead:
    """WhatsApp is a client-side `wa.me` deep link followed by this call.

    Nothing in the response may imply delivery — the product handed the
    operator a composed message and recorded that it did.
    """
    _require(scope, "actions")
    from app.services.actions import ActionWriter

    kinds = {
        "WHATSAPP": SystemActionKind.WHATSAPP_SENT,
        "SMS": SystemActionKind.SMS_SENT,
        "EMAIL": SystemActionKind.EMAIL_SENT,
    }
    channel = str(payload.get("channel", "")).upper()
    if channel not in kinds:
        raise api_error(422, "unknown_channel", "Channel must be WHATSAPP, SMS or EMAIL")

    lead = await service.get_lead(lead_id)
    writer = ActionWriter(scope.session, actor_id=scope.membership_id)
    await writer.open_changeset(
        source=ChangesetSource.SINGLE_EDIT,
        summary=f"{channel.title()} composed for {lead.identity_value}",
    )
    template_id = payload.get("template_id")
    action = writer.record_message(
        lead,
        kind=kinds[channel],
        body=str(payload.get("body", "")),
        template_id=uuid.UUID(str(template_id)) if template_id else None,
    )
    await scope.session.commit()
    return ActionRead.model_validate(action)
