"""Роуты организаций: настройки, участники, приглашения.

Права проверяются зависимостями ``OrgMemberContext`` / ``OrgAdminContext`` /
``OrgOwnerContext``: к моменту входа в тело обработчика членство и роль уже
подтверждены, а объект организации получен. Роут не обращается к чужим
данным - он работает только с тем, что дал контекст.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import (
    CsrfProtected,
    DbDep,
    OrgAdminContext,
    OrgMemberContext,
    OrgServiceDep,
    VerifiedUser,
)
from app.schemas.common import Message
from app.schemas.org import (
    InvitationAccept,
    InvitationCreate,
    InvitationOut,
    MemberOut,
    MemberRoleUpdate,
    OrgCreate,
    OrgOut,
    OrgSummary,
    OrgUpdate,
)

router = APIRouter(prefix="/orgs", tags=["organizations"])


@router.get("", summary="Мои организации")
async def list_my_orgs(user: VerifiedUser, orgs: OrgServiceDep) -> list[OrgSummary]:
    rows = await orgs.list_for_user(user)
    return [OrgSummary.model_validate(row) for row in rows]


@router.post("", status_code=201, dependencies=[CsrfProtected], summary="Создать организацию")
async def create_org(
    payload: OrgCreate, user: VerifiedUser, orgs: OrgServiceDep, db: DbDep
) -> OrgOut:
    org = await orgs.create(
        user, name=payload.name, slug=payload.slug, description=payload.description
    )
    await db.commit()
    return OrgOut.model_validate(org)


@router.get("/{slug}", summary="Организация")
async def get_org(ctx: OrgMemberContext) -> OrgOut:
    return OrgOut.model_validate(ctx.org)


@router.patch("/{slug}", dependencies=[CsrfProtected], summary="Изменить настройки организации")
async def update_org(
    payload: OrgUpdate, ctx: OrgAdminContext, orgs: OrgServiceDep, db: DbDep
) -> OrgOut:
    org = await orgs.update(ctx.org, ctx.user, payload.model_dump(exclude_unset=True))
    await db.commit()
    return OrgOut.model_validate(org)


# Участники


@router.get("/{slug}/members", summary="Участники организации")
async def list_members(ctx: OrgMemberContext, orgs: OrgServiceDep) -> list[MemberOut]:
    rows = await orgs.list_members(ctx.org)
    return [MemberOut.model_validate(row) for row in rows]


@router.patch(
    "/{slug}/members/{user_id}",
    dependencies=[CsrfProtected],
    summary="Изменить роль участника",
)
async def change_member_role(
    user_id: uuid.UUID,
    payload: MemberRoleUpdate,
    ctx: OrgAdminContext,
    orgs: OrgServiceDep,
    db: DbDep,
) -> Message:
    await orgs.change_role(ctx.org, ctx.user, ctx.role, user_id, payload.role)
    await db.commit()
    return Message(detail="Роль обновлена")


@router.delete(
    "/{slug}/members/{user_id}",
    dependencies=[CsrfProtected],
    summary="Исключить участника (или выйти самому)",
)
async def remove_member(
    user_id: uuid.UUID,
    ctx: OrgMemberContext,
    orgs: OrgServiceDep,
    db: DbDep,
) -> Message:
    # Проверку прав делает сервис: выйти самому может любой участник,
    # исключить другого - только старший по роли.
    await orgs.remove_member(ctx.org, ctx.user, ctx.role, user_id)
    await db.commit()
    return Message(detail="Участник удалён из организации")


# Приглашения


@router.get("/{slug}/invitations", summary="Активные приглашения")
async def list_invitations(ctx: OrgAdminContext, orgs: OrgServiceDep) -> list[InvitationOut]:
    invitations = await orgs.list_invitations(ctx.org)
    return [InvitationOut.model_validate(i) for i in invitations]


@router.post(
    "/{slug}/invitations",
    status_code=201,
    dependencies=[CsrfProtected],
    summary="Пригласить участника",
)
async def invite_member(
    payload: InvitationCreate,
    ctx: OrgAdminContext,
    orgs: OrgServiceDep,
    db: DbDep,
) -> InvitationOut:
    invitation = await orgs.invite(
        ctx.org, ctx.user, ctx.role, email=payload.email, role=payload.role
    )
    await db.commit()
    return InvitationOut.model_validate(invitation)


@router.delete(
    "/{slug}/invitations/{invitation_id}",
    dependencies=[CsrfProtected],
    summary="Отозвать приглашение",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    ctx: OrgAdminContext,
    orgs: OrgServiceDep,
    db: DbDep,
) -> Message:
    await orgs.revoke_invitation(ctx.org, ctx.user, invitation_id)
    await db.commit()
    return Message(detail="Приглашение отозвано")


@router.post(
    "/invitations/accept",
    dependencies=[CsrfProtected],
    summary="Принять приглашение по токену",
)
async def accept_invitation(
    payload: InvitationAccept, user: VerifiedUser, orgs: OrgServiceDep, db: DbDep
) -> OrgOut:
    org = await orgs.accept_invitation(user, payload.token)
    await db.commit()
    return OrgOut.model_validate(org)
