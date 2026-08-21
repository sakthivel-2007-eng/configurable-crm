"""Sales groups, assignment rules and distribution endpoints (M8).

`docs/02-api-contract.md` §Settings/sales-groups, §Settings/assignment-rules,
and `POST /leads/distribute`.

Two capability groups, deliberately: groups sit under `team` because they are a
shape of the sales team and M9 reports segment by them, while the rules that
read them sit under `automations`. An operator who may see who is in which group
is not thereby allowed to redirect every incoming lead.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.errors import api_error
from app.schemas.routing import (
    AssignmentPreviewRead,
    AssignmentRuleCreate,
    AssignmentRuleRead,
    AssignmentRuleReorder,
    AssignmentRuleUpdate,
    DistributeRequest,
    DistributionRead,
    SalesGroupCreate,
    SalesGroupMemberRead,
    SalesGroupMemberWrite,
    SalesGroupRead,
    SalesGroupUpdate,
)
from app.services.routing import (
    AssignmentRuleService,
    DistributionService,
    SalesGroupService,
)
from app.tenancy.scoping import WorkspaceScope, require_workspace

router = APIRouter(tags=["routing"])


def _require(scope: WorkspaceScope, group: str, capability: str) -> None:
    if not scope.capability(group, capability):
        raise api_error(
            403,
            "insufficient_permissions",
            f"This permission template does not allow: {capability.replace('_', ' ')}",
        )


# --- sales groups ------------------------------------------------------------


@router.get("/settings/sales-groups", response_model=list[SalesGroupRead], summary="Sales groups")
async def list_sales_groups(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    include_archived: Annotated[bool, Query()] = False,
) -> list[SalesGroupRead]:
    _require(scope, "team", "view_members")
    groups = await SalesGroupService(scope.session).list_groups(include_archived=include_archived)
    return [SalesGroupRead.model_validate(group) for group in groups]


@router.post(
    "/settings/sales-groups",
    response_model=SalesGroupRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a sales group",
)
async def create_sales_group(
    body: SalesGroupCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> SalesGroupRead:
    _require(scope, "team", "manage_sales_groups")
    group = await SalesGroupService(scope.session).create(
        name=body.name, description=body.description
    )
    await scope.session.commit()
    return SalesGroupRead.model_validate(group)


@router.patch(
    "/settings/sales-groups/{group_id}",
    response_model=SalesGroupRead,
    summary="Update a sales group",
)
async def update_sales_group(
    group_id: uuid.UUID,
    body: SalesGroupUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> SalesGroupRead:
    _require(scope, "team", "manage_sales_groups")
    group = await SalesGroupService(scope.session).update(
        group_id,
        name=body.name,
        description=body.description,
        is_archived=body.is_archived,
    )
    await scope.session.commit()
    return SalesGroupRead.model_validate(group)


@router.delete(
    "/settings/sales-groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a sales group",
)
async def archive_sales_group(
    group_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> None:
    _require(scope, "team", "manage_sales_groups")
    await SalesGroupService(scope.session).archive(group_id)
    await scope.session.commit()


@router.get(
    "/settings/sales-groups/{group_id}/members",
    response_model=list[SalesGroupMemberRead],
    summary="Members of a sales group",
)
async def list_group_members(
    group_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[SalesGroupMemberRead]:
    _require(scope, "team", "view_members")
    rows = await SalesGroupService(scope.session).members(group_id)
    return [SalesGroupMemberRead.model_validate(row) for row in rows]


@router.put(
    "/settings/sales-groups/{group_id}/members",
    response_model=list[SalesGroupMemberRead],
    summary="Replace a sales group's members",
)
async def set_group_members(
    group_id: uuid.UUID,
    body: list[SalesGroupMemberWrite],
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[SalesGroupMemberRead]:
    _require(scope, "team", "manage_sales_groups")
    rows = await SalesGroupService(scope.session).set_members(
        group_id, [row.model_dump() for row in body]
    )
    await scope.session.commit()
    return [SalesGroupMemberRead.model_validate(row) for row in rows]


# --- assignment rules --------------------------------------------------------


def _rules(scope: WorkspaceScope) -> AssignmentRuleService:
    return AssignmentRuleService(scope.session, workspace=scope.workspace)


@router.get(
    "/settings/assignment-rules",
    response_model=list[AssignmentRuleRead],
    summary="Assignment rules, in priority order",
)
async def list_assignment_rules(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[AssignmentRuleRead]:
    _require(scope, "automations", "view_automations")
    rules = await _rules(scope).list_rules()
    return [AssignmentRuleRead.model_validate(rule) for rule in rules]


@router.post(
    "/settings/assignment-rules",
    response_model=AssignmentRuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an assignment rule",
)
async def create_assignment_rule(
    body: AssignmentRuleCreate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> AssignmentRuleRead:
    _require(scope, "automations", "manage_assignment_rules")
    rule = await _rules(scope).create(
        name=body.name,
        strategy=body.strategy,
        config=body.config,
        conditions=body.conditions,
        priority=body.priority,
        skip_unavailable=body.skip_unavailable,
        is_active=body.is_active,
    )
    await scope.session.commit()
    return AssignmentRuleRead.model_validate(rule)


@router.patch(
    "/settings/assignment-rules/reorder",
    response_model=list[AssignmentRuleRead],
    summary="Reorder assignment rules",
)
async def reorder_assignment_rules(
    body: AssignmentRuleReorder,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> list[AssignmentRuleRead]:
    # Declared before `/{rule_id}` so "reorder" is not read as a rule id.
    _require(scope, "automations", "manage_assignment_rules")
    rules = await _rules(scope).reorder(body.order)
    await scope.session.commit()
    return [AssignmentRuleRead.model_validate(rule) for rule in rules]


@router.post(
    "/settings/assignment-rules/preview",
    response_model=AssignmentPreviewRead,
    summary="Dry run: which rule would fire for a lead",
)
async def preview_assignment(
    lead_id: Annotated[uuid.UUID, Query(description="An existing lead to test against")],
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> AssignmentPreviewRead:
    """Assigns nothing.

    It does advance a round-robin cursor, because it runs the real engine — a
    preview that dodged the cursor would be previewing behaviour that does not
    exist. Call it on demand, not on every keystroke.
    """
    _require(scope, "automations", "view_automations")
    outcome = await _rules(scope).preview(lead_id)
    await scope.session.commit()
    return AssignmentPreviewRead(
        rule_id=outcome.rule_id,
        rule_name=outcome.rule_name,
        membership_id=outcome.membership_id,
        reason=outcome.reason,
    )


@router.patch(
    "/settings/assignment-rules/{rule_id}",
    response_model=AssignmentRuleRead,
    summary="Update an assignment rule",
)
async def update_assignment_rule(
    rule_id: uuid.UUID,
    body: AssignmentRuleUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> AssignmentRuleRead:
    _require(scope, "automations", "manage_assignment_rules")
    rule = await _rules(scope).update(rule_id, **body.model_dump(exclude_unset=True))
    await scope.session.commit()
    return AssignmentRuleRead.model_validate(rule)


@router.delete(
    "/settings/assignment-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate an assignment rule",
)
async def delete_assignment_rule(
    rule_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> None:
    _require(scope, "automations", "manage_assignment_rules")
    await _rules(scope).delete(rule_id)
    await scope.session.commit()


# --- distribution ------------------------------------------------------------


@router.post(
    "/leads/distribute",
    response_model=DistributionRead,
    summary="Redistribute existing leads",
)
async def distribute_leads(
    body: DistributeRequest,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
) -> DistributionRead:
    """One changeset, so the whole redistribution can be undone as a unit."""
    _require(scope, "automations", "distribute_leads")
    service = DistributionService(
        scope.session, workspace=scope.workspace, actor_id=scope.membership_id
    )
    result = await service.distribute(
        lead_ids=body.lead_ids,
        strategy=body.strategy,
        config=body.config,
        skip_unavailable=body.skip_unavailable,
    )
    await scope.session.commit()
    return DistributionRead(
        changeset_id=result.changeset_id,
        assigned=result.assigned,
        skipped=result.skipped,
        total=result.total,
    )
