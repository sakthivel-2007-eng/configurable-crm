"""Field-definition endpoints (M2).

`docs/02-api-contract.md` §Settings/Fields. Thin, as the conventions require:
every rule lives in `FieldService`, and every query is scoped by
`require_workspace_admin` before it reaches here.

The two registry endpoints (`/settings/field-types`,
`/settings/action-field-types`) are the reason the frontend never hardcodes a
type list.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status

from app.fields.registry import action_type_payloads, lead_type_payloads
from app.models.enums import LeadFieldType
from app.schemas.field import (
    FieldOptionBulkCreate,
    FieldOptionCreate,
    FieldOptionRead,
    FieldOptionReorder,
    FieldOptionUpdate,
    FieldTypeRead,
    IdentityFieldUpdate,
    IndexedFieldCreate,
    IndexedFieldRead,
    LeadFieldCreate,
    LeadFieldRead,
    LeadFieldUpdate,
    PrimaryFieldsUpdate,
)
from app.services.fields import FieldService
from app.tenancy.scoping import WorkspaceScope, require_workspace, require_workspace_admin
from app.workers.indexing import remove_declared_index, run_declared_index_build

# Mounted under the tenant prefix in `main`, matching every other tenant
# router — the workspace segment is applied there, not repeated here.
router = APIRouter(prefix="/settings", tags=["settings: fields"])


def _service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> FieldService:
    return FieldService(scope.session)


def _admin_service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
) -> FieldService:
    return FieldService(scope.session)


# --- the registries ----------------------------------------------------------


@router.get(
    "/field-types",
    response_model=list[FieldTypeRead],
    summary="The 13-type lead field registry",
)
async def list_field_types(
    # Any member may read the registry — the lead form needs it to render.
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[FieldTypeRead]:
    return [FieldTypeRead(**payload) for payload in lead_type_payloads()]


@router.get(
    "/action-field-types",
    response_model=list[FieldTypeRead],
    summary="The 8-type action field registry",
)
async def list_action_field_types(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[FieldTypeRead]:
    return [FieldTypeRead(**payload) for payload in action_type_payloads()]


# --- lead fields -------------------------------------------------------------


@router.get("/lead-fields", response_model=list[LeadFieldRead], summary="List lead fields")
async def list_lead_fields(
    service: Annotated[FieldService, Depends(_service)],
    search: Annotated[str | None, Query(max_length=80)] = None,
    field_type: Annotated[LeadFieldType | None, Query()] = None,
    include_hidden: Annotated[bool, Query()] = False,
) -> list[LeadFieldRead]:
    fields = await service.list_fields(
        search=search, field_type=field_type, include_hidden=include_hidden
    )
    return [LeadFieldRead.model_validate(f) for f in fields]


@router.post(
    "/lead-fields",
    response_model=LeadFieldRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a lead field",
)
async def create_lead_field(
    payload: LeadFieldCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> LeadFieldRead:
    field = await service.create_field(**payload.model_dump())
    await scope.session.commit()
    return LeadFieldRead.model_validate(await service.get_field(field.id))


@router.get("/lead-fields/{field_id}", response_model=LeadFieldRead, summary="One lead field")
async def get_lead_field(
    field_id: uuid.UUID,
    service: Annotated[FieldService, Depends(_service)],
) -> LeadFieldRead:
    return LeadFieldRead.model_validate(await service.get_field(field_id))


@router.patch(
    "/lead-fields/{field_id}", response_model=LeadFieldRead, summary="Update a lead field"
)
async def update_lead_field(
    field_id: uuid.UUID,
    payload: LeadFieldUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> LeadFieldRead:
    await service.update_field(field_id, **payload.model_dump(exclude_unset=True))
    await scope.session.commit()
    return LeadFieldRead.model_validate(await service.get_field(field_id))


@router.post("/lead-fields/{field_id}/hide", response_model=LeadFieldRead, summary="Hide a field")
async def hide_lead_field(
    field_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> LeadFieldRead:
    await service.set_hidden(field_id, hidden=True)
    await scope.session.commit()
    # Re-read so `options` is eager-loaded: serialising the instance the
    # service returned would lazy-load outside the async context.
    return LeadFieldRead.model_validate(await service.get_field(field_id))


@router.post(
    "/lead-fields/{field_id}/unhide", response_model=LeadFieldRead, summary="Unhide a field"
)
async def unhide_lead_field(
    field_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> LeadFieldRead:
    await service.set_hidden(field_id, hidden=False)
    await scope.session.commit()
    return LeadFieldRead.model_validate(await service.get_field(field_id))


# --- options -----------------------------------------------------------------


@router.get(
    "/lead-fields/{field_id}/options",
    response_model=list[FieldOptionRead],
    summary="List a field's options",
)
async def list_options(
    field_id: uuid.UUID,
    service: Annotated[FieldService, Depends(_service)],
) -> list[FieldOptionRead]:
    return [FieldOptionRead.model_validate(o) for o in await service.list_options(field_id)]


@router.post(
    "/lead-fields/{field_id}/options",
    response_model=FieldOptionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add an option",
)
async def add_option(
    field_id: uuid.UUID,
    payload: FieldOptionCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> FieldOptionRead:
    option = await service.add_option(field_id, **payload.model_dump())
    await scope.session.commit()
    return FieldOptionRead.model_validate(option)


@router.post(
    "/lead-fields/{field_id}/options/bulk",
    response_model=list[FieldOptionRead],
    status_code=status.HTTP_201_CREATED,
    summary='"Add multiple" — one option per line',
)
async def add_options_bulk(
    field_id: uuid.UUID,
    payload: FieldOptionBulkCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> list[FieldOptionRead]:
    created = await service.add_options_bulk(field_id, labels=payload.labels)
    await scope.session.commit()
    return [FieldOptionRead.model_validate(o) for o in created]


@router.post(
    "/lead-fields/{field_id}/options/copy-from/{source_field_id}",
    response_model=list[FieldOptionRead],
    status_code=status.HTTP_201_CREATED,
    summary='"Copy options" — clone another field\'s option set',
)
async def copy_options(
    field_id: uuid.UUID,
    source_field_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> list[FieldOptionRead]:
    created = await service.copy_options_from(field_id, source_field_id=source_field_id)
    await scope.session.commit()
    return [FieldOptionRead.model_validate(o) for o in created]


# Declared before `/options/{option_id}`: FastAPI matches in declaration
# order, and a literal path segment must win over a uuid placeholder or
# "reorder" is parsed as an option id.
@router.patch(
    "/lead-fields/{field_id}/options/reorder",
    response_model=list[FieldOptionRead],
    summary="Drag-reorder a field's options",
)
async def reorder_options(
    field_id: uuid.UUID,
    payload: FieldOptionReorder,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> list[FieldOptionRead]:
    options = await service.reorder_options(field_id, ordered_ids=payload.ordered_ids)
    await scope.session.commit()
    return [FieldOptionRead.model_validate(o) for o in options]


@router.patch(
    "/lead-fields/{field_id}/options/{option_id}",
    response_model=FieldOptionRead,
    summary="Rename or recolour an option",
)
async def update_option(
    field_id: uuid.UUID,
    option_id: uuid.UUID,
    payload: FieldOptionUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> FieldOptionRead:
    await service.list_options(field_id)  # 404s the field before touching the option
    option = await service.update_option(option_id, **payload.model_dump(exclude_unset=True))
    await scope.session.commit()
    return FieldOptionRead.model_validate(option)


@router.delete(
    "/lead-fields/{field_id}/options/{option_id}",
    response_model=FieldOptionRead,
    summary="Archive an option (never deletes)",
)
async def archive_option(
    field_id: uuid.UUID,
    option_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> FieldOptionRead:
    await service.list_options(field_id)
    option = await service.archive_option(option_id)
    await scope.session.commit()
    return FieldOptionRead.model_validate(option)


# --- workspace-level field settings ------------------------------------------


@router.put("/identity-field", summary="Designate the lead identity field")
async def set_identity_field(
    payload: IdentityFieldUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> dict[str, str]:
    workspace = await service.set_identity_field(payload.field_id)
    await scope.session.commit()
    # M5 owns `identity_value`; when it exists, changing this enqueues a
    # backfill. Reported here so the UI can warn before the data catches up.
    return {
        "identity_field_id": str(workspace.identity_field_id),
        "backfill": "pending",
    }


@router.put("/primary-fields", summary="Set the H1/H2 headline fields")
async def set_primary_fields(
    payload: PrimaryFieldsUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> dict[str, str | None]:
    workspace = await service.set_primary_fields(
        h1_field_id=payload.h1_field_id, h2_field_id=payload.h2_field_id
    )
    await scope.session.commit()
    return {
        "h1_field_id": str(workspace.primary_field_1_id),
        "h2_field_id": (
            str(workspace.primary_field_2_id) if workspace.primary_field_2_id else None
        ),
    }


# --- indexed fields ----------------------------------------------------------


@router.get(
    "/indexed-fields",
    response_model=list[IndexedFieldRead],
    summary="Fields declared sortable/filterable",
)
async def list_indexed_fields(
    service: Annotated[FieldService, Depends(_service)],
) -> list[IndexedFieldRead]:
    return [IndexedFieldRead.model_validate(i) for i in await service.list_indexed()]


@router.post(
    "/indexed-fields",
    response_model=IndexedFieldRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Declare a field indexed (returns PENDING)",
)
async def declare_indexed_field(
    payload: IndexedFieldCreate,
    request: Request,
    background: BackgroundTasks,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> IndexedFieldRead:
    """202, not 201: the row exists but the index does not yet.

    `CREATE INDEX CONCURRENTLY` cannot run in this request's transaction, so
    the worker builds it and flips the status.
    """
    entry = await service.declare_indexed(payload.field_id)
    field = await service.get_field(payload.field_id)
    await scope.session.commit()

    # Scheduled after the commit, so the worker cannot look for a declaration
    # this request has not written yet. A `BackgroundTasks` job rather than an
    # `arq` enqueue: there is no worker process yet, and M8 brings one up for
    # the scheduler — at which point this call site swaps for an enqueue and
    # nothing else changes.
    background.add_task(
        run_declared_index_build,
        request.app.state.engine,
        request.app.state.session_factory,
        workspace_id=scope.workspace.id,
        field_id=payload.field_id,
        field_key=field.key,
    )
    return IndexedFieldRead.model_validate(entry)


@router.delete(
    "/indexed-fields/{field_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Un-declare an indexed field",
)
async def undeclare_indexed_field(
    field_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[FieldService, Depends(_admin_service)],
) -> dict[str, str]:
    # The declaration goes now; the index itself is dropped by the worker,
    # concurrently, for the same reason it was built there.
    name = await service.undeclare_indexed(field_id)
    await scope.session.commit()

    background.add_task(
        remove_declared_index,
        request.app.state.engine,
        workspace_id=scope.workspace.id,
        field_id=field_id,
    )
    return {"index_name": name, "status": "DROPPING"}
