"""Saved filters and table layouts (M6).

`docs/02-api-contract.md` §Filters, layouts, labels, tasks — the filters and
layouts halves. Labels and tasks belong to M7.

Thin, like every other router here: `ViewService` owns the visibility rule and
`LeadService` owns running a filter, because a filter's *stats* are a lead read
and have to pass through the same projection every other lead read does.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.schemas.filter import (
    FilterStats,
    LayoutRead,
    LayoutWrite,
    SavedFilterCreate,
    SavedFilterRead,
    SavedFilterReorder,
    SavedFilterUpdate,
)
from app.services.leads import LeadService
from app.services.views import ViewService
from app.tenancy.scoping import WorkspaceScope, require_workspace

router = APIRouter(tags=["filters"])


async def _view_service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> ViewService:
    return ViewService(
        scope.session,
        membership_id=scope.membership_id,
        template_id=scope.template.id,
        is_admin=scope.sees_all_members,
    )


async def _lead_service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> LeadService:
    """Same construction as the leads router — the caller's chokepoints bound.

    Stats run a filter, which is a lead read. Building the service any other
    way here would be the "internal caller" that skips projection.
    """
    return LeadService(
        scope.session,
        workspace=scope.workspace,
        projection=await scope.projection(),
        write_filter=await scope.write_filter(),
        actor_id=scope.membership_id,
        visible_membership_ids=scope.visible_membership_ids,
        sees_all=scope.sees_all_members,
    )


# --- saved filters -----------------------------------------------------------


@router.get("/filters", response_model=list[SavedFilterRead], summary="Filters visible to me")
async def list_filters(
    service: Annotated[ViewService, Depends(_view_service)],
    include_archived: Annotated[bool, Query()] = False,
) -> list[SavedFilterRead]:
    return [
        SavedFilterRead.model_validate(f)
        for f in await service.list_filters(include_archived=include_archived)
    ]


@router.post(
    "/filters",
    response_model=SavedFilterRead,
    status_code=status.HTTP_201_CREATED,
    summary="Save a filter",
)
async def create_filter(
    payload: SavedFilterCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ViewService, Depends(_view_service)],
) -> SavedFilterRead:
    saved = await service.create_filter(
        name=payload.name,
        description=payload.description,
        definition=payload.definition,
        visibility=payload.visibility,
        template_id=payload.template_id,
    )
    await scope.session.commit()
    return SavedFilterRead.model_validate(saved)


@router.patch("/filters/reorder", response_model=list[SavedFilterRead], summary="Reorder filters")
async def reorder_filters(
    payload: SavedFilterReorder,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ViewService, Depends(_view_service)],
) -> list[SavedFilterRead]:
    """Declared before `/filters/{filter_id}` — conventions §5."""
    ordered = await service.reorder_filters(payload.filter_ids)
    await scope.session.commit()
    return [SavedFilterRead.model_validate(f) for f in ordered]


@router.get("/filters/{filter_id}", response_model=SavedFilterRead, summary="One saved filter")
async def get_filter(
    filter_id: uuid.UUID,
    service: Annotated[ViewService, Depends(_view_service)],
) -> SavedFilterRead:
    return SavedFilterRead.model_validate(await service.get_filter(filter_id))


@router.patch("/filters/{filter_id}", response_model=SavedFilterRead, summary="Edit a saved filter")
async def update_filter(
    filter_id: uuid.UUID,
    payload: SavedFilterUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ViewService, Depends(_view_service)],
) -> SavedFilterRead:
    saved = await service.update_filter(
        filter_id,
        name=payload.name,
        description=payload.description,
        definition=payload.definition,
        visibility=payload.visibility,
        template_id=payload.template_id,
        # "not mentioned" and "explicitly cleared" are different instructions,
        # and JSON alone cannot tell them apart.
        template_id_given="template_id" in payload.model_fields_set,
    )
    await scope.session.commit()
    return SavedFilterRead.model_validate(saved)


@router.post(
    "/filters/{filter_id}/duplicate",
    response_model=SavedFilterRead,
    status_code=status.HTTP_201_CREATED,
    summary="Copy a filter into my own",
)
async def duplicate_filter(
    filter_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ViewService, Depends(_view_service)],
) -> SavedFilterRead:
    copy = await service.duplicate_filter(filter_id)
    await scope.session.commit()
    return SavedFilterRead.model_validate(copy)


@router.delete("/filters/{filter_id}", response_model=SavedFilterRead, summary="Archive a filter")
async def archive_filter(
    filter_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ViewService, Depends(_view_service)],
) -> SavedFilterRead:
    """Archived, never deleted — a layout and, from M8, a scheduled report may
    still reference the id (architecture rule 13)."""
    saved = await service.archive_filter(filter_id)
    await scope.session.commit()
    return SavedFilterRead.model_validate(saved)


@router.get(
    "/filters/{filter_id}/stats",
    response_model=FilterStats,
    summary="How many leads this filter matches",
)
async def filter_stats(
    filter_id: uuid.UUID,
    views: Annotated[ViewService, Depends(_view_service)],
    leads: Annotated[LeadService, Depends(_lead_service)],
) -> FilterStats:
    """Counted through the caller's own grants and visibility.

    Two members can legitimately see different totals for the same shared
    filter, and that is correct rather than a bug: the count is an answer, and
    answers are per-caller.
    """
    saved = await views.get_filter(filter_id)
    node = views.parse_definition(saved)
    clause = await leads.compile_filter(node)
    total, by_stage = await leads.count_by_stage(clause)
    return FilterStats(filter_id=filter_id, total=total, by_stage=by_stage)


# --- table layouts -----------------------------------------------------------


@router.get("/layouts", response_model=LayoutRead | None, summary="My columns for a filter")
async def get_layout(
    service: Annotated[ViewService, Depends(_view_service)],
    filter_id: Annotated[uuid.UUID | None, Query()] = None,
) -> LayoutRead | None:
    layout = await service.get_layout(filter_id)
    return LayoutRead.model_validate(layout) if layout else None


@router.put("/layouts", response_model=LayoutRead, summary="Save my columns for a filter")
async def put_layout(
    payload: LayoutWrite,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ViewService, Depends(_view_service)],
    filter_id: Annotated[uuid.UUID | None, Query()] = None,
) -> LayoutRead:
    layout = await service.put_layout(
        filter_id,
        columns=payload.columns,
        column_widths=payload.column_widths,
        sort_key=payload.sort_key,
        sort_desc=payload.sort_desc,
    )
    await scope.session.commit()
    return LayoutRead.model_validate(layout)
