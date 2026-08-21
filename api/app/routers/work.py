"""Tasks, labels and spreadsheet imports (M7).

`docs/02-api-contract.md` §Filters, layouts, labels, tasks — the labels and
tasks halves — plus `POST /leads/import` and the job endpoints behind it.

Thin, like every other router here. The one thing worth noticing is the shape of
the import flow: **upload, map, preview, commit** are four separate requests,
not one. An operator has to be able to see what a file would do before it does
it, and has to be able to walk away between the preview and the decision.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status

from app.errors import api_error
from app.models.enums import ImportJobKind
from app.models.work import ImportJob
from app.schemas.common import Page
from app.schemas.work import (
    ImportJobRead,
    ImportMappingWrite,
    LabelCreate,
    LabelRead,
    LabelUpdate,
    TaskCounts,
    TaskCreate,
    TaskRead,
    TaskUpdate,
)
from app.services.importing import ImportService
from app.services.work import LabelService, TaskBucket, TaskService
from app.tenancy.scoping import WorkspaceScope, require_workspace

router = APIRouter(tags=["work"])

#: Uploads are read fully into memory to be parsed, so the ceiling is real
#: rather than advisory. 20,000 rows of leads is comfortably under this.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


async def _task_service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> TaskService:
    return TaskService(
        scope.session,
        workspace=scope.workspace,
        actor_id=scope.membership_id,
        visible_membership_ids=scope.visible_membership_ids,
        sees_all=scope.sees_all_members,
    )


async def _label_service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> LabelService:
    return LabelService(scope.session)


async def _import_service(
    request: Request,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> ImportService:
    """Bound to the caller's write filter, which is what limits the mapping.

    The S3 client comes off app state rather than being constructed here: the
    uploaded sheet has to survive between the four steps of the flow, and the
    lifespan owns that client.
    """
    return ImportService(
        scope.session,
        workspace=scope.workspace,
        write_filter=await scope.write_filter(),
        actor_id=scope.membership_id,
        storage=getattr(request.app.state, "s3", None),
        bucket=request.app.state.settings.s3_bucket,
    )


def _require(scope: WorkspaceScope, group: str, capability: str) -> None:
    if not scope.capability(group, capability):
        raise api_error(
            403,
            "insufficient_permissions",
            f"This permission template does not allow: {capability.replace('_', ' ')}",
        )


# --- tasks -------------------------------------------------------------------


@router.get("/tasks", response_model=Page[TaskRead], summary="Tasks, by bucket")
async def list_tasks(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[TaskService, Depends(_task_service)],
    bucket: Annotated[TaskBucket | None, Query()] = None,
    assignee_id: Annotated[uuid.UUID | None, Query()] = None,
    lead_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TaskRead]:
    """`upcoming`, `late` and `done` are computed against the workspace's
    timezone, never stored — see `TaskService`."""
    _require(scope, "tasks", "view_tasks")
    tasks, total = await service.list_tasks(
        limit=limit, offset=offset, bucket=bucket, assignee_id=assignee_id, lead_id=lead_id
    )
    return Page(
        items=[TaskRead.model_validate(t) for t in tasks],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/tasks/counts", response_model=TaskCounts, summary="How many in each bucket")
async def task_counts(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[TaskService, Depends(_task_service)],
) -> TaskCounts:
    """Declared before `/tasks/{task_id}` — conventions §5."""
    _require(scope, "tasks", "view_tasks")
    return TaskCounts(**await service.counts())


@router.post(
    "/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
)
async def create_task(
    payload: TaskCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[TaskService, Depends(_task_service)],
) -> TaskRead:
    """A task on a lead also writes to that lead's timeline (rule 5)."""
    _require(scope, "tasks", "create_tasks")
    task = await service.create_task(
        title=payload.title,
        due_at=payload.due_at,
        lead_id=payload.lead_id,
        notes=payload.notes,
        assignee_id=payload.assignee_id,
    )
    await scope.session.commit()
    return TaskRead.model_validate(task)


@router.get("/tasks/{task_id}", response_model=TaskRead, summary="One task")
async def get_task(
    task_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[TaskService, Depends(_task_service)],
) -> TaskRead:
    _require(scope, "tasks", "view_tasks")
    return TaskRead.model_validate(await service.get_task(task_id))


@router.patch("/tasks/{task_id}", response_model=TaskRead, summary="Edit a task")
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[TaskService, Depends(_task_service)],
) -> TaskRead:
    _require(scope, "tasks", "create_tasks")
    task = await service.update_task(
        task_id,
        title=payload.title,
        notes=payload.notes,
        due_at=payload.due_at,
        assignee_id=payload.assignee_id,
        # "not mentioned" and "explicitly nobody" are different instructions.
        assignee_given="assignee_id" in payload.model_fields_set,
    )
    await scope.session.commit()
    return TaskRead.model_validate(task)


@router.post("/tasks/{task_id}/complete", response_model=TaskRead, summary="Mark a task done")
async def complete_task(
    task_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[TaskService, Depends(_task_service)],
) -> TaskRead:
    _require(scope, "tasks", "complete_tasks")
    task = await service.complete_task(task_id)
    await scope.session.commit()
    return TaskRead.model_validate(task)


@router.post("/tasks/{task_id}/reopen", response_model=TaskRead, summary="Reopen a task")
async def reopen_task(
    task_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[TaskService, Depends(_task_service)],
) -> TaskRead:
    _require(scope, "tasks", "complete_tasks")
    task = await service.reopen_task(task_id)
    await scope.session.commit()
    return TaskRead.model_validate(task)


@router.get("/leads/{lead_id}/tasks", response_model=list[TaskRead], summary="A lead's tasks")
async def lead_tasks(
    lead_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[TaskService, Depends(_task_service)],
) -> list[TaskRead]:
    _require(scope, "tasks", "view_tasks")
    tasks, _ = await service.list_tasks(limit=100, offset=0, lead_id=lead_id)
    return [TaskRead.model_validate(t) for t in tasks]


# --- labels ------------------------------------------------------------------


@router.get("/labels", response_model=list[LabelRead], summary="The workspace's labels")
async def list_labels(
    service: Annotated[LabelService, Depends(_label_service)],
    include_archived: Annotated[bool, Query()] = False,
) -> list[LabelRead]:
    return [
        LabelRead.model_validate(label)
        for label in await service.list_labels(include_archived=include_archived)
    ]


@router.post(
    "/labels",
    response_model=LabelRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a label",
)
async def create_label(
    payload: LabelCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LabelService, Depends(_label_service)],
) -> LabelRead:
    label = await service.create_label(name=payload.name, color=payload.color)
    await scope.session.commit()
    return LabelRead.model_validate(label)


@router.patch("/labels/{label_id}", response_model=LabelRead, summary="Rename a label")
async def update_label(
    label_id: uuid.UUID,
    payload: LabelUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LabelService, Depends(_label_service)],
) -> LabelRead:
    label = await service.update_label(label_id, name=payload.name, color=payload.color)
    await scope.session.commit()
    return LabelRead.model_validate(label)


@router.delete("/labels/{label_id}", response_model=LabelRead, summary="Archive a label")
async def archive_label(
    label_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LabelService, Depends(_label_service)],
) -> LabelRead:
    """Archived, not deleted: deleting would strip the label from every lead
    carrying it, unanswerably (rule 13)."""
    label = await service.archive_label(label_id)
    await scope.session.commit()
    return LabelRead.model_validate(label)


@router.get("/leads/{lead_id}/labels", response_model=list[LabelRead], summary="A lead's labels")
async def lead_labels(
    lead_id: uuid.UUID,
    service: Annotated[LabelService, Depends(_label_service)],
) -> list[LabelRead]:
    return [LabelRead.model_validate(label) for label in await service.labels_for(lead_id)]


@router.post(
    "/leads/{lead_id}/labels/{label_id}",
    response_model=list[LabelRead],
    summary="Put a label on a lead",
)
async def attach_label(
    lead_id: uuid.UUID,
    label_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LabelService, Depends(_label_service)],
) -> list[LabelRead]:
    labels = await service.attach(lead_id, label_id)
    await scope.session.commit()
    return [LabelRead.model_validate(label) for label in labels]


@router.delete(
    "/leads/{lead_id}/labels/{label_id}",
    response_model=list[LabelRead],
    summary="Take a label off a lead",
)
async def detach_label(
    lead_id: uuid.UUID,
    label_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[LabelService, Depends(_label_service)],
) -> list[LabelRead]:
    labels = await service.detach(lead_id, label_id)
    await scope.session.commit()
    return [LabelRead.model_validate(label) for label in labels]


# --- imports -----------------------------------------------------------------


@router.get("/imports/fields", summary="Fields this caller may map")
async def importable_fields(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ImportService, Depends(_import_service)],
) -> list[dict[str, Any]]:
    """Both conditions: `show_in_import` *and* the caller's Import grant.

    Declared before `/imports/{job_id}` — conventions §5. This is what the
    mapping UI populates its target list from, so the screen cannot offer a
    field the commit would then refuse.
    """
    _require(scope, "leads", "add_or_update")
    return [
        {"key": field.key, "label": field.label, "field_type": field.field_type.value}
        for field in await service.importable_fields()
    ]


@router.post(
    "/imports",
    response_model=ImportJobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a spreadsheet",
)
async def create_import(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ImportService, Depends(_import_service)],
    file: Annotated[UploadFile, File()],
    kind: Annotated[ImportJobKind, Query()] = ImportJobKind.LEAD_IMPORT,
) -> ImportJobRead:
    """Step one of four: upload, map, preview, commit."""
    _require(scope, "leads", "add_or_update")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise api_error(
            413,
            "file_too_large",
            f"Uploads are limited to {MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
        )

    job, _sheet = await service.create_job(
        kind=kind, filename=file.filename or "upload.csv", content=content
    )
    await scope.session.commit()
    return ImportJobRead.model_validate(job)


@router.get("/imports", response_model=Page[ImportJobRead], summary="Recent import runs")
async def list_imports(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ImportJobRead]:
    rows, total = await scope.session.list(
        ImportJob, limit=limit, offset=offset, order_by=ImportJob.created_at.desc()
    )
    return Page(
        items=[ImportJobRead.model_validate(job) for job in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/imports/{job_id}", response_model=ImportJobRead, summary="One import run")
async def get_import(
    job_id: uuid.UUID,
    service: Annotated[ImportService, Depends(_import_service)],
) -> ImportJobRead:
    return ImportJobRead.model_validate(await service.get_job(job_id))


@router.put(
    "/imports/{job_id}/mapping",
    response_model=ImportJobRead,
    summary="Choose how columns map",
)
async def set_mapping(
    job_id: uuid.UUID,
    payload: ImportMappingWrite,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ImportService, Depends(_import_service)],
) -> ImportJobRead:
    """Refused if the mapping names a field the caller cannot import."""
    _require(scope, "leads", "add_or_update")
    job = await service.set_mapping(job_id, mapping=payload.mapping, options=payload.options)
    await scope.session.commit()
    return ImportJobRead.model_validate(job)


@router.post(
    "/imports/{job_id}/preview",
    response_model=ImportJobRead,
    summary="Dry run — what would this do?",
)
async def preview_import(
    job_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ImportService, Depends(_import_service)],
) -> ImportJobRead:
    """Create-versus-update counts and the rows that would fail, written nowhere.

    The answer to "Pitfalls of Excel Upload": an operator who sees "180 create,
    4 update, 6 errors" before committing does not have to undo afterwards.
    """
    _require(scope, "leads", "add_or_update")
    job = await service.preview(job_id)
    await scope.session.commit()
    return ImportJobRead.model_validate(job)


@router.post(
    "/imports/{job_id}/commit",
    response_model=ImportJobRead,
    summary="Apply the import as one changeset",
)
async def commit_import(
    job_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[ImportService, Depends(_import_service)],
) -> ImportJobRead:
    """One changeset for the whole run, so a bad import is one undo."""
    _require(scope, "leads", "add_or_update")
    job = await service.commit(job_id)
    await scope.session.commit()
    return ImportJobRead.model_validate(job)
