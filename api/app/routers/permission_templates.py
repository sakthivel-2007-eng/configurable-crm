"""Permission template reads.

M1 exposes the list only, because inviting a member requires naming a template
and there is otherwise no way to discover the five a workspace was provisioned
with.

M4 owns the rest of this resource: create, update, delete, the assignee list,
and the per-field View/Edit/Import/Export matrix. Do not add them here — the
matrix cannot be built before `lead_fields` exists, and a half-template editor
is worse than none.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.models import PermissionTemplate
from app.schemas.permission_template import PermissionTemplateSummary
from app.tenancy.scoping import WorkspaceScope, require_workspace

router = APIRouter(tags=["settings"])


@router.get(
    "/settings/permission-templates",
    response_model=list[PermissionTemplateSummary],
    summary="Permission templates in this workspace",
)
async def list_permission_templates(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[PermissionTemplateSummary]:
    """Every template this workspace has authored.

    Readable by any member, not just admins: a member list renders each
    person's template name, and hiding the names would leave the UI unable to
    label its own rows.
    """
    rows = await scope.session.execute(
        scope.session.select(PermissionTemplate).order_by(PermissionTemplate.name)
    )
    return [PermissionTemplateSummary.model_validate(template) for template in rows.scalars().all()]
