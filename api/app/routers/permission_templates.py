"""Permission template endpoints.

M1 exposed the list only, because inviting a member requires naming a template.
M4 added the rest: create, update, delete, assignees, the per-field
View/Edit/Import/Export matrix with its column rollups, and "Set up your lead
view".

Every rule lives in `PermissionTemplateService`; this file owns the HTTP shape.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.models import PermissionTemplate
from app.models.enums import PermissionGrant
from app.permissions.capabilities import (
    ACCESS_GROUPS,
    PROPOSED_GROUPS,
    VIEW_GROUPS,
    Capabilities,
)
from app.schemas.permission_template import (
    BulkGrantUpdate,
    FieldGrantsUpdate,
    LeadViewUpdate,
    PermissionTemplateCreate,
    PermissionTemplateDetail,
    PermissionTemplateSummary,
    PermissionTemplateUpdate,
)
from app.services.permissions import PermissionTemplateService
from app.tenancy.scoping import WorkspaceScope, require_workspace, require_workspace_admin

router = APIRouter(prefix="/settings/permission-templates", tags=["settings: permissions"])


def _service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> PermissionTemplateService:
    return PermissionTemplateService(scope.session)


def _admin_service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
) -> PermissionTemplateService:
    return PermissionTemplateService(scope.session)


@router.get(
    "",
    response_model=list[PermissionTemplateSummary],
    summary="Permission templates in this workspace",
)
async def list_permission_templates(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[PermissionTemplateSummary]:
    """Readable by any member, not just admins.

    A member list renders each person's template name; hiding the names would
    leave the UI unable to label its own rows.
    """
    rows = await scope.session.execute(
        scope.session.select(PermissionTemplate).order_by(PermissionTemplate.name)
    )
    return [PermissionTemplateSummary.model_validate(t) for t in rows.scalars().all()]


@router.get(
    "/capability-schema",
    summary="The 10 Access groups and 3 View groups, with proposed ones flagged",
)
async def capability_schema(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> dict[str, Any]:
    """What the template editor renders its Access and View sections from.

    `proposed: true` marks a group whose contents this codebase proposed rather
    than observed — §8 lists nine of the thirteen as "Not inspected", and
    PROMPTS.md M4 asks for exactly this flag so the set can be reviewed rather
    than mistaken for observation.

    Declared before `/{template_id}` so the literal segment wins over the uuid
    placeholder.
    """
    blank = Capabilities()

    def describe(group: str, model: Any) -> dict[str, Any]:
        return {
            "key": group,
            "proposed": group in PROPOSED_GROUPS,
            "capabilities": sorted(model.__class__.model_fields),
        }

    return {
        "access": [describe(g, getattr(blank, g)) for g in ACCESS_GROUPS],
        "view": [describe(g, getattr(blank.view, g)) for g in VIEW_GROUPS],
    }


@router.post(
    "",
    response_model=PermissionTemplateDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a permission template",
)
async def create_template(
    payload: PermissionTemplateCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[PermissionTemplateService, Depends(_admin_service)],
) -> PermissionTemplateDetail:
    template = await service.create_template(name=payload.name, capabilities=payload.capabilities)
    await scope.session.commit()
    return PermissionTemplateDetail.model_validate(template)


@router.get(
    "/{template_id}", response_model=PermissionTemplateDetail, summary="One permission template"
)
async def get_template(
    template_id: uuid.UUID,
    service: Annotated[PermissionTemplateService, Depends(_service)],
) -> PermissionTemplateDetail:
    return PermissionTemplateDetail.model_validate(await service.get_template(template_id))


@router.patch(
    "/{template_id}",
    response_model=PermissionTemplateDetail,
    summary="Update a template (403 on Root)",
)
async def update_template(
    template_id: uuid.UUID,
    payload: PermissionTemplateUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[PermissionTemplateService, Depends(_admin_service)],
) -> PermissionTemplateDetail:
    template = await service.update_template(
        template_id,
        name=payload.name,
        capabilities=payload.capabilities,
        updated_by_id=scope.membership.user_id,
    )
    await scope.session.commit()
    return PermissionTemplateDetail.model_validate(template)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unassigned template (409 if assigned)",
)
async def delete_template(
    template_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[PermissionTemplateService, Depends(_admin_service)],
) -> None:
    await service.delete_template(template_id)
    await scope.session.commit()


@router.get("/{template_id}/field-grants", summary="The field matrix plus per-column rollups")
async def get_field_grants(
    template_id: uuid.UUID,
    service: Annotated[PermissionTemplateService, Depends(_service)],
) -> dict[str, Any]:
    return await service.field_matrix(template_id)


@router.put("/{template_id}/field-grants", summary="Replace the grants for the fields named")
async def replace_field_grants(
    template_id: uuid.UUID,
    payload: FieldGrantsUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[PermissionTemplateService, Depends(_admin_service)],
) -> dict[str, Any]:
    await service.replace_field_grants(
        template_id,
        grants=[g.model_dump(by_alias=True) for g in payload.grants],
    )
    await scope.session.commit()
    return await service.field_matrix(template_id)


@router.put(
    "/{template_id}/field-grants/bulk",
    summary="Column select-all: one grant across many fields",
)
async def bulk_set_grant(
    template_id: uuid.UUID,
    payload: BulkGrantUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[PermissionTemplateService, Depends(_admin_service)],
) -> dict[str, Any]:
    await service.bulk_set_grant(
        template_id,
        grant=PermissionGrant(payload.grant),
        value=payload.value,
        field_ids=payload.field_ids,
    )
    await scope.session.commit()
    return await service.field_matrix(template_id)


@router.get(
    "/{template_id}/lead-view",
    summary='"Set up your lead view" - the per-template detail layout',
)
async def get_lead_view(
    template_id: uuid.UUID,
    service: Annotated[PermissionTemplateService, Depends(_service)],
) -> dict[str, Any]:
    return {"layout": await service.get_lead_view(template_id)}


@router.put("/{template_id}/lead-view", summary="Replace the lead-detail layout")
async def set_lead_view(
    template_id: uuid.UUID,
    payload: LeadViewUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[PermissionTemplateService, Depends(_admin_service)],
) -> dict[str, Any]:
    layout = await service.set_lead_view(
        template_id, layout=[g.model_dump() for g in payload.layout]
    )
    await scope.session.commit()
    return {"layout": layout}


@router.get("/{template_id}/assignees", summary="Who holds this template")
async def list_assignees(
    template_id: uuid.UUID,
    service: Annotated[PermissionTemplateService, Depends(_service)],
) -> list[dict[str, Any]]:
    members = await service.list_assignees(template_id)
    return [
        {
            "membership_id": str(m.id),
            "user": {
                "id": str(m.user.id),
                "email": m.user.email,
                "full_name": m.user.full_name,
            },
            "is_active": m.is_active,
        }
        for m in members
    ]
