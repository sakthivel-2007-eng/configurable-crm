"""API keys, webhooks, the outbox viewer and the intake log (M10).

`docs/02-api-contract.md` §Outbound: `/settings/webhooks` CRUD + `/test`,
`/settings/api-keys` (plaintext once), `/settings/outbox` with `/retry`, and
`/settings/intake-log`.

Both secrets in this module — an API key and a webhook signing secret — are
returned exactly once, at creation. There is no "show me that again": the row
holds an Argon2 hash of the first and the second is never needed here again. An
operator who loses one rotates it, which is the behaviour you want anyway.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select

from app.auth.api_keys import generate_key
from app.auth.passwords import PasswordHasherService
from app.errors import api_error
from app.events.dispatcher import DeliveryResult, HttpxTransport
from app.events.envelope import EVENT_NAMES, serialise, sign
from app.models import (
    ApiKey,
    IntakeLogEntry,
    OutboxEvent,
    OutboxStatus,
    PermissionTemplate,
    WebhookEndpoint,
)
from app.schemas.common import Page, PageParams, page_params
from app.schemas.integration import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    IntakeLogRead,
    OutboxEventRead,
    WebhookCreate,
    WebhookCreated,
    WebhookRead,
    WebhookTestResult,
    WebhookUpdate,
)
from app.tenancy.scoping import WorkspaceScope, require_workspace

router = APIRouter(tags=["integrations"])


def _require(scope: WorkspaceScope, group: str, capability: str) -> None:
    if not scope.capability(group, capability):
        raise api_error(
            403,
            "insufficient_permissions",
            f"This permission template does not allow: {capability.replace('_', ' ')}",
        )


async def _assert_template(scope: WorkspaceScope, template_id: uuid.UUID) -> None:
    template = await scope.session.get(PermissionTemplate, template_id)
    if template is None:
        raise api_error(422, "unknown_template", "No such permission template here")


# --- API keys ----------------------------------------------------------------


@router.get("/settings/api-keys", response_model=list[ApiKeyRead], summary="API keys")
async def list_api_keys(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[ApiKeyRead]:
    _require(scope, "automations", "manage_api_keys")
    rows = await scope.session.execute(
        scope.session.select(ApiKey).order_by(ApiKey.created_at.desc())
    )
    return [ApiKeyRead.model_validate(row) for row in rows.scalars().all()]


@router.post(
    "/settings/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
)
async def create_api_key(
    body: ApiKeyCreate,
    request: Request,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> ApiKeyCreated:
    """The plaintext is in this response and nowhere else, ever."""
    _require(scope, "automations", "manage_api_keys")
    await _assert_template(scope, body.permission_template_id)

    existing = await scope.session.execute(
        scope.session.select(ApiKey).where(func.lower(ApiKey.name) == body.name.lower())
    )
    if existing.scalars().first() is not None:
        raise api_error(409, "duplicate_key_name", f"A key named {body.name!r} exists")

    hasher = request.app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    secret, prefix = generate_key()

    key = ApiKey(
        name=body.name,
        prefix=prefix,
        hashed_key=hasher.hash(secret),
        permission_template_id=body.permission_template_id,
        created_by=scope.membership_id,
    )
    scope.session.add(key)
    await scope.session.commit()
    return ApiKeyCreated(**ApiKeyRead.model_validate(key).model_dump(), key=secret)


@router.delete(
    "/settings/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
)
async def revoke_api_key(
    key_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> None:
    """Revoked, not deleted.

    The intake log references it, and "which key posted this bad data" is the
    first question after an incident.
    """
    _require(scope, "automations", "manage_api_keys")
    key = await scope.session.get(ApiKey, key_id)
    if key is None:
        raise api_error(404, "key_not_found", "No such API key")
    key.revoked_at = dt.datetime.now(dt.UTC)
    await scope.session.commit()


# --- webhooks ----------------------------------------------------------------


@router.get("/settings/webhooks", response_model=list[WebhookRead], summary="Webhooks")
async def list_webhooks(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[WebhookRead]:
    _require(scope, "automations", "manage_webhooks")
    rows = await scope.session.execute(
        scope.session.select(WebhookEndpoint).order_by(WebhookEndpoint.name)
    )
    return [WebhookRead.model_validate(row) for row in rows.scalars().all()]


@router.get("/settings/webhooks/events", response_model=list[str], summary="Event names")
async def list_event_names(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[str]:
    """So the UI offers the real list rather than a hand-copied one."""
    _require(scope, "automations", "manage_webhooks")
    return sorted(EVENT_NAMES)


@router.post(
    "/settings/webhooks",
    response_model=WebhookCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook endpoint",
)
async def create_webhook(
    body: WebhookCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> WebhookCreated:
    _require(scope, "automations", "manage_webhooks")
    await _assert_template(scope, body.permission_template_id)

    unknown = set(body.events) - EVENT_NAMES
    if unknown:
        # Unlike intake, strictness is right here: a subscription to an event
        # that will never fire is silent forever, and the operator configuring
        # it is present to be told.
        raise api_error(422, "unknown_event", f"Not events this product emits: {sorted(unknown)}")
    if not body.url.startswith(("http://", "https://")):
        raise api_error(422, "invalid_url", "A webhook URL must be http or https")

    endpoint = WebhookEndpoint(
        name=body.name,
        url=body.url,
        secret=f"whsec_{secrets.token_urlsafe(32)}",
        events=list(body.events),
        permission_template_id=body.permission_template_id,
    )
    scope.session.add(endpoint)
    await scope.session.commit()
    return WebhookCreated(
        **WebhookRead.model_validate(endpoint).model_dump(), secret=endpoint.secret
    )


@router.patch(
    "/settings/webhooks/{endpoint_id}",
    response_model=WebhookRead,
    summary="Update a webhook endpoint",
)
async def update_webhook(
    endpoint_id: uuid.UUID,
    body: WebhookUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> WebhookRead:
    _require(scope, "automations", "manage_webhooks")
    endpoint = await scope.session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise api_error(404, "webhook_not_found", "No such webhook")

    if body.events is not None:
        unknown = set(body.events) - EVENT_NAMES
        if unknown:
            raise api_error(
                422, "unknown_event", f"Not events this product emits: {sorted(unknown)}"
            )
    if body.permission_template_id is not None:
        await _assert_template(scope, body.permission_template_id)

    for attribute in ("name", "url", "events", "permission_template_id", "is_active"):
        value = getattr(body, attribute)
        if value is not None:
            setattr(endpoint, attribute, value)
    await scope.session.commit()
    return WebhookRead.model_validate(endpoint)


@router.delete(
    "/settings/webhooks/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook endpoint",
)
async def delete_webhook(
    endpoint_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> None:
    _require(scope, "automations", "manage_webhooks")
    endpoint = await scope.session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise api_error(404, "webhook_not_found", "No such webhook")
    endpoint.is_active = False
    await scope.session.commit()


@router.post(
    "/settings/webhooks/{endpoint_id}/test",
    response_model=WebhookTestResult,
    summary="Send a test delivery",
)
async def test_webhook(
    endpoint_id: uuid.UUID,
    request: Request,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> WebhookTestResult:
    """Deliberately synchronous, and the only place that is true.

    Rule 8 forbids calling out from a request handler because a *user's* action
    must not depend on a third party being up. This is not a user's action — it
    is an operator asking "is my endpoint reachable", and an answer that arrived
    later in a log would not answer it.
    """
    _require(scope, "automations", "manage_webhooks")
    endpoint = await scope.session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise api_error(404, "webhook_not_found", "No such webhook")

    test_event_id = str(uuid.uuid4())
    envelope: dict[str, object] = {
        "event": "lead.created",
        "event_id": test_event_id,
        "workspace_id": str(scope.workspace_id),
        "occurred_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "data": {"test": True},
    }
    body = serialise(envelope)
    signature = sign(endpoint.secret, body)

    transport = getattr(request.app.state, "webhook_transport", None) or HttpxTransport()
    result: DeliveryResult = await transport.post(
        endpoint.url,
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-CRM-Event": "lead.created",
            "X-CRM-Event-Id": test_event_id,
            "X-CRM-Signature": signature,
        },
    )
    return WebhookTestResult(
        delivered=result.ok,
        status_code=result.status_code,
        error=result.error,
        signature=signature,
    )


# --- the outbox viewer -------------------------------------------------------


@router.get("/settings/outbox", response_model=Page[OutboxEventRead], summary="Queued events")
async def list_outbox(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    page: Annotated[PageParams, Depends(page_params)],
    outbox_status: Annotated[OutboxStatus | None, Query(alias="status")] = None,
) -> Page[OutboxEventRead]:
    _require(scope, "automations", "view_automations")
    stmt = scope.session.select(OutboxEvent).order_by(OutboxEvent.occurred_at.desc())
    count_stmt = (
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.workspace_id == scope.workspace_id)
    )
    if outbox_status is not None:
        stmt = stmt.where(OutboxEvent.status == outbox_status)
        count_stmt = count_stmt.where(OutboxEvent.status == outbox_status)

    total = int((await scope.session.execute(count_stmt)).scalar() or 0)
    rows = await scope.session.execute(stmt.limit(page.limit).offset(page.offset))
    return Page(
        items=[OutboxEventRead.model_validate(row) for row in rows.scalars().all()],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/settings/outbox/{event_id}/retry",
    response_model=OutboxEventRead,
    summary="Redrive a dead or failed event",
)
async def retry_outbox_event(
    event_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> OutboxEventRead:
    """Puts the row back in the queue; the worker does the sending.

    Resets `attempts`, because an operator redriving a DEAD event has usually
    just fixed the thing that broke it and wants the full budget again, not the
    one attempt left over from four hours ago.
    """
    _require(scope, "automations", "manage_webhooks")
    row = await scope.session.get(OutboxEvent, event_id)
    if row is None:
        raise api_error(404, "event_not_found", "No such outbox event")
    if row.status is OutboxStatus.DELIVERED:
        raise api_error(409, "already_delivered", "That event was already delivered")

    row.status = OutboxStatus.PENDING
    row.attempts = 0
    row.claimed_at = None
    row.next_attempt_at = dt.datetime.now(dt.UTC)
    row.last_error = None
    await scope.session.commit()
    return OutboxEventRead.model_validate(row)


# --- the intake log ----------------------------------------------------------


@router.get("/settings/intake-log", response_model=Page[IntakeLogRead], summary="Intake log")
async def list_intake_log(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    page: Annotated[PageParams, Depends(page_params)],
    rejected_only: Annotated[bool, Query()] = False,
) -> Page[IntakeLogRead]:
    """Rejections included — they are what people come here to find."""
    _require(scope, "automations", "view_intake_log")
    stmt = scope.session.select(IntakeLogEntry).order_by(IntakeLogEntry.created_at.desc())
    count_stmt = (
        select(func.count())
        .select_from(IntakeLogEntry)
        .where(IntakeLogEntry.workspace_id == scope.workspace_id)
    )
    if rejected_only:
        from app.models import IntakeOutcome

        stmt = stmt.where(IntakeLogEntry.outcome == IntakeOutcome.REJECTED)
        count_stmt = count_stmt.where(IntakeLogEntry.outcome == IntakeOutcome.REJECTED)

    total = int((await scope.session.execute(count_stmt)).scalar() or 0)
    rows = await scope.session.execute(stmt.limit(page.limit).offset(page.offset))
    return Page(
        items=[IntakeLogRead.model_validate(row) for row in rows.scalars().all()],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
