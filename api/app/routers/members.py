"""Member, licensing and availability endpoints.

Everything here is mounted under `/workspaces/{workspace_id}` and depends on
`require_workspace` or `require_workspace_admin`. There is no path to a
membership that does not first resolve a workspace scope, which is what makes
the cross-workspace 404 a property of the wiring rather than of each handler
remembering to check.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy import select

from app.auth.deps import get_password_hasher
from app.auth.passwords import PasswordHasherService
from app.errors import conflict, unprocessable
from app.models import Membership, User
from app.schemas.common import Page, PageParams, page_params
from app.schemas.member import (
    AvailabilityLogEntry,
    AvailabilityUpdate,
    BulkUploadReport,
    DeactivateRequest,
    DeactivateResponse,
    HierarchyNodeOut,
    MemberDetail,
    MemberInvite,
    MemberUpdate,
    SeatUsage,
)
from app.services.lead_ownership import LeadOwnership, get_lead_ownership
from app.services.member_import import MemberImportService
from app.services.members import HierarchyNode, MemberService
from app.tenancy.scoping import WorkspaceScope, require_workspace, require_workspace_admin

router = APIRouter(tags=["members"])

# A member roster is not lead data — capping the upload keeps one request from
# holding a transaction open across thousands of rows.
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _member_service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    lead_ownership: Annotated[LeadOwnership, Depends(get_lead_ownership)],
) -> MemberService:
    return MemberService(scope.session, lead_ownership=lead_ownership)


def _admin_member_service(
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    lead_ownership: Annotated[LeadOwnership, Depends(get_lead_ownership)],
) -> MemberService:
    return MemberService(scope.session, lead_ownership=lead_ownership)


def _detail(membership: Membership) -> MemberDetail:
    return MemberDetail(
        id=membership.id,
        workspace_id=membership.workspace_id,
        user=membership.user,
        template_id=membership.template_id,
        template_name=membership.template.name,
        manager_id=membership.manager_id,
        is_active=membership.is_active,
        has_license=membership.has_license,
        availability=membership.availability,
        created_at=membership.created_at,
    )


def _node(node: HierarchyNode) -> HierarchyNodeOut:
    return HierarchyNodeOut(
        member=_detail(node.membership),
        reports=[_node(child) for child in node.reports],
    )


@router.get(
    "/members",
    response_model=Page[MemberDetail],
    summary="List members",
)
async def list_members(
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[MemberService, Depends(_member_service)],
    page: Annotated[PageParams, Depends(page_params)],
    include_inactive: Annotated[bool, Query()] = True,
) -> Page[MemberDetail]:
    """Members this caller may see.

    An admin sees the whole workspace; anyone else sees themselves and their
    reports. The narrowing set comes from `WorkspaceScope`, resolved once by
    the scoping layer — this handler does not walk the hierarchy itself.
    """
    visible = None if scope.sees_all_members else scope.visible_membership_ids
    members, total = await service.list_members(
        limit=page.limit,
        offset=page.offset,
        include_inactive=include_inactive,
        visible_membership_ids=visible,
    )
    return Page(
        items=[_detail(member) for member in members],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/members/hierarchy",
    response_model=list[HierarchyNodeOut],
    summary="The manager tree",
)
async def member_hierarchy(
    service: Annotated[MemberService, Depends(_member_service)],
) -> list[HierarchyNodeOut]:
    return [_node(node) for node in await service.hierarchy()]


@router.get(
    "/members/seats",
    response_model=SeatUsage,
    summary="Licensed seats used and available",
)
async def seat_usage(
    service: Annotated[MemberService, Depends(_member_service)],
) -> SeatUsage:
    used, limit = await service.seat_usage()
    return SeatUsage(seats_used=used, seat_limit=limit)


@router.post(
    "/members",
    response_model=MemberDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a user into the workspace",
)
async def invite_member(
    payload: MemberInvite,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[MemberService, Depends(_admin_member_service)],
    hasher: Annotated[PasswordHasherService, Depends(get_password_hasher)],
) -> MemberDetail:
    """Invite by email, creating the user account if it does not exist yet.

    A newly created account gets a random password it cannot be told: the
    invitee arrives through a password reset, so no credential ever exists in
    two places.
    """
    email = str(payload.email).casefold()
    existing = await scope.session.execute(select(User).where(User.email == email).limit(1))
    user = existing.scalar_one_or_none()

    if user is None:
        user = User(
            email=email,
            full_name=payload.full_name,
            password_hash=hasher.hash(uuid.uuid4().hex + uuid.uuid4().hex),
        )
        scope.session.add_global(user)
        await scope.session.flush()

    membership = await service.invite(
        user=user,
        template_id=payload.template_id,
        manager_id=payload.manager_id,
        grant_license=payload.grant_license,
    )
    await scope.session.commit()
    return _detail(await service.get(membership.id))


@router.get(
    "/members/{membership_id}",
    response_model=MemberDetail,
    summary="Member detail",
)
async def get_member(
    membership_id: uuid.UUID,
    service: Annotated[MemberService, Depends(_member_service)],
) -> MemberDetail:
    return _detail(await service.get(membership_id))


@router.patch(
    "/members/{membership_id}",
    response_model=MemberDetail,
    summary="Update a member's template, manager or name",
)
async def update_member(
    membership_id: uuid.UUID,
    payload: MemberUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[MemberService, Depends(_admin_member_service)],
) -> MemberDetail:
    membership = await service.get(membership_id)

    if payload.template_id is not None:
        await service.set_template(membership_id, payload.template_id)

    if payload.clear_manager:
        await service.set_manager(membership_id, None)
    elif payload.manager_id is not None:
        await service.set_manager(membership_id, payload.manager_id)

    if payload.full_name is not None:
        # The display name lives on the user, which is global — but it is only
        # reachable here through a membership this scope already authorised.
        membership.user.full_name = payload.full_name

    await scope.session.commit()
    return _detail(await service.get(membership_id))


@router.post(
    "/members/{membership_id}/license",
    response_model=MemberDetail,
    summary="Assign a licensed seat",
)
async def assign_license(
    membership_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[MemberService, Depends(_admin_member_service)],
) -> MemberDetail:
    await service.assign_license(membership_id)
    await scope.session.commit()
    return _detail(await service.get(membership_id))


@router.delete(
    "/members/{membership_id}/license",
    response_model=MemberDetail,
    summary="Revoke a licensed seat",
)
async def revoke_license(
    membership_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[MemberService, Depends(_admin_member_service)],
) -> MemberDetail:
    await service.revoke_license(membership_id)
    await scope.session.commit()
    return _detail(await service.get(membership_id))


@router.put(
    "/members/{membership_id}/availability",
    response_model=MemberDetail,
    summary="Set availability (WORKING / ON_LEAVE / INACTIVE)",
)
async def set_availability(
    membership_id: uuid.UUID,
    payload: AvailabilityUpdate,
    scope: Annotated[WorkspaceScope, Depends(require_workspace)],
    service: Annotated[MemberService, Depends(_member_service)],
) -> MemberDetail:
    """Members set their own availability; admins set anyone's.

    Marking someone INACTIVE through this endpoint is refused — that is
    deactivation, and deactivation has to reassign their pipeline first.
    """
    if membership_id != scope.membership_id and not scope.is_workspace_admin:
        raise unprocessable(
            "not_own_membership",
            "You may only change your own availability",
        )

    from app.models import AvailabilityStatus

    if payload.status is AvailabilityStatus.INACTIVE:
        raise conflict(
            "use_deactivate_endpoint",
            "Set INACTIVE through POST /members/{id}/deactivate so open leads are reassigned",
        )

    await service.set_availability(
        membership_id,
        status=payload.status,
        note=payload.note,
        changed_by_id=scope.membership_id,
    )
    await scope.session.commit()
    return _detail(await service.get(membership_id))


@router.get(
    "/members/{membership_id}/availability-log",
    response_model=Page[AvailabilityLogEntry],
    summary="Availability history",
)
async def availability_log(
    membership_id: uuid.UUID,
    service: Annotated[MemberService, Depends(_member_service)],
    page: Annotated[PageParams, Depends(page_params)],
) -> Page[AvailabilityLogEntry]:
    entries, total = await service.availability_log(
        membership_id,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(
        items=[AvailabilityLogEntry.model_validate(entry) for entry in entries],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/members/{membership_id}/deactivate",
    response_model=DeactivateResponse,
    summary="Deactivate a member, reassigning their open leads",
)
async def deactivate_member(
    membership_id: uuid.UUID,
    payload: DeactivateRequest,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[MemberService, Depends(_admin_member_service)],
) -> DeactivateResponse:
    """Refuses with `409 reassignment_required` when the member holds open
    leads and no `reassign_to_membership_id` was given. Never orphan a
    pipeline."""
    if membership_id == scope.membership_id:
        raise conflict(
            "cannot_deactivate_self",
            "You cannot deactivate your own membership",
        )

    membership, reassigned = await service.deactivate(
        membership_id,
        reassign_to_membership_id=payload.reassign_to_membership_id,
        changed_by_id=scope.membership_id,
    )
    await scope.session.commit()
    return DeactivateResponse(
        member=_detail(await service.get(membership.id)),
        leads_reassigned=reassigned,
    )


@router.post(
    "/members/{membership_id}/reactivate",
    response_model=MemberDetail,
    summary="Reactivate a member, consuming a free seat",
)
async def reactivate_member(
    membership_id: uuid.UUID,
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    service: Annotated[MemberService, Depends(_admin_member_service)],
) -> MemberDetail:
    await service.reactivate(membership_id, changed_by_id=scope.membership_id)
    await scope.session.commit()
    return _detail(await service.get(membership_id))


@router.post(
    "/members/bulk-upload",
    response_model=BulkUploadReport,
    summary="Create members from an Excel workbook",
)
async def bulk_upload_members(
    scope: Annotated[WorkspaceScope, Depends(require_workspace_admin)],
    hasher: Annotated[PasswordHasherService, Depends(get_password_hasher)],
    file: Annotated[UploadFile, File(description="XLSX with email, full_name, template columns")],
    dry_run: Annotated[bool, Query(description="Preview without writing")] = True,
) -> BulkUploadReport:
    """Dry run by default — see the outcome before committing it."""
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise unprocessable(
            "file_too_large",
            f"The upload exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )

    service = MemberImportService(scope.session, hasher=hasher)
    report = await service.run(content, dry_run=dry_run)
    if not dry_run:
        await scope.session.commit()
    return report
