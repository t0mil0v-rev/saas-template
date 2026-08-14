"""Административный контур.

Доступ только суперпользователю (зависимость ``SuperUser``). Это сквозной,
надтенантный контур: администратор платформы видит все организации и всех
пользователей. Поэтому каждое действие здесь особенно тщательно пишется в
аудит, а сам маршрут вынесен под отдельный префикс ``/admin``.

Правило: администратор не может лишить прав или отключить сам себя -
иначе одним неверным запросом платформа остаётся без единого админа.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import Executable, func, select, update

from app.api.deps import CsrfProtected, DbDep, SuperUser
from app.core.errors import ConflictError, NotFoundError
from app.schemas.admin import (
    AdminOrgOut,
    AdminUserOut,
    AuditEntryOut,
    SystemStats,
)
from app.schemas.common import Message, Page
from app.services import audit

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats", summary="Сводка по системе")
async def system_stats(admin: SuperUser, db: DbDep) -> SystemStats:
    from app.db.models import Organization, Review, ReviewStatus, User

    async def count(stmt: Executable) -> int:
        return int(await db.scalar(stmt) or 0)

    return SystemStats(
        users_total=await count(select(func.count()).select_from(User)),
        users_active=await count(
            select(func.count()).select_from(User).where(User.is_active.is_(True))
        ),
        orgs_total=await count(select(func.count()).select_from(Organization)),
        reviews_total=await count(select(func.count()).select_from(Review)),
        reviews_pending=await count(
            select(func.count())
            .select_from(Review)
            .where(Review.status == ReviewStatus.PENDING.value)
        ),
    )


# Пользователи


@router.get("/users", summary="Список пользователей")
async def list_users(
    admin: SuperUser,
    db: DbDep,
    q: str = Query(default="", max_length=320),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> Page[AdminUserOut]:
    from app.db.models import OrgMember, User

    orgs_count = (
        select(func.count())
        .select_from(OrgMember)
        .where(OrgMember.user_id == User.id)
        .scalar_subquery()
    )
    base = select(User, orgs_count)
    if q:
        # ILIKE с экранированными спецсимволами LIKE: иначе '%' в вводе
        # превращает поиск в полное сканирование по всем адресам.
        needle = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        base = base.where(User.email.ilike(f"%{needle}%", escape="\\"))

    total = int(await db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = (
        await db.execute(base.order_by(User.created_at.desc()).limit(limit).offset(offset))
    ).all()
    items = [
        AdminUserOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            totp_enabled=user.totp_enabled,
            email_verified_at=user.email_verified_at,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            orgs_count=count,
        )
        for user, count in rows
    ]
    return Page[AdminUserOut](items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/users/{user_id}/deactivate",
    dependencies=[CsrfProtected],
    summary="Отключить пользователя",
)
async def deactivate_user(user_id: uuid.UUID, admin: SuperUser, db: DbDep) -> Message:
    from app.db.models import Organization, OrgMember, OrgRole, Session, User

    if user_id == admin.id:
        raise ConflictError("Нельзя отключить собственную учётную запись")

    # Деактивация затрагивает глобальный инвариант superuser и может менять
    # активных владельцев нескольких организаций. Один lock задаёт порядок
    # до row locks и не даёт двум администраторам заблокировать друг друга.
    await db.execute(select(func.pg_advisory_xact_lock(1515870811)))
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise NotFoundError("Пользователь не найден")
    if not user.is_active:
        return Message(detail="Пользователь уже отключён")

    if user.is_superuser:
        active_superusers = list(
            (
                await db.scalars(
                    select(User)
                    .where(User.is_superuser.is_(True), User.is_active.is_(True))
                    .order_by(User.id)
                    .with_for_update()
                )
            ).all()
        )
        if len(active_superusers) <= 1:
            raise ConflictError("Нельзя отключить последнего активного администратора")

    owned_org_ids = list(
        (
            await db.scalars(
                select(OrgMember.org_id).where(
                    OrgMember.user_id == user.id,
                    OrgMember.role == OrgRole.OWNER.value,
                )
            )
        ).all()
    )
    if owned_org_ids:
        await db.scalars(
            select(Organization.id)
            .where(Organization.id.in_(owned_org_ids))
            .order_by(Organization.id)
            .with_for_update()
        )
        owner_rows = (
            await db.execute(
                select(OrgMember.org_id, func.count())
                .join(User, User.id == OrgMember.user_id)
                .where(
                    OrgMember.org_id.in_(owned_org_ids),
                    OrgMember.role == OrgRole.OWNER.value,
                    User.is_active.is_(True),
                )
                .group_by(OrgMember.org_id)
            )
        ).all()
        active_owner_counts: dict[uuid.UUID, int] = {
            org_id: int(count) for org_id, count in owner_rows
        }
        if any(active_owner_counts.get(org_id, 0) <= 1 for org_id in owned_org_ids):
            raise ConflictError(
                "Пользователь остаётся единственным активным владельцем организации. "
                "Сначала назначьте или включите другого владельца."
            )

    user.is_active = False
    # Отключение немедленно завершает все его сеансы.
    await db.execute(
        update(Session)
        .where(Session.user_id == user.id, Session.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    await audit.record(
        db,
        audit.Action.USER_DEACTIVATED,
        actor_user_id=admin.id,
        target_type="user",
        target_id=str(user_id),
    )
    await db.commit()
    return Message(detail="Пользователь отключён, его сеансы завершены")


@router.post(
    "/users/{user_id}/activate",
    dependencies=[CsrfProtected],
    summary="Включить пользователя",
)
async def activate_user(user_id: uuid.UUID, admin: SuperUser, db: DbDep) -> Message:
    from app.db.models import User

    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("Пользователь не найден")
    user.is_active = True
    user.failed_login_count = 0
    user.locked_until = None
    await audit.record(
        db,
        audit.Action.USER_ACTIVATED,
        actor_user_id=admin.id,
        target_type="user",
        target_id=str(user_id),
    )
    await db.commit()
    return Message(detail="Пользователь включён")


# Организации


@router.get("/orgs", summary="Все организации")
async def list_orgs(
    admin: SuperUser,
    db: DbDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> Page[AdminOrgOut]:
    from app.db.models import Organization, OrgMember, Review

    members_count = (
        select(func.count())
        .select_from(OrgMember)
        .where(OrgMember.org_id == Organization.id)
        .scalar_subquery()
    )
    reviews_count = (
        select(func.count())
        .select_from(Review)
        .where(Review.org_id == Organization.id)
        .scalar_subquery()
    )
    base = select(Organization, members_count, reviews_count)
    total = int(await db.scalar(select(func.count()).select_from(Organization)) or 0)
    rows = (
        await db.execute(base.order_by(Organization.created_at.desc()).limit(limit).offset(offset))
    ).all()
    items = [
        AdminOrgOut(
            id=org.id,
            slug=org.slug,
            name=org.name,
            plan=org.plan,
            is_public=org.is_public,
            members_count=members,
            reviews_count=reviews,
            created_at=org.created_at,
        )
        for org, members, reviews in rows
    ]
    return Page[AdminOrgOut](items=items, total=total, limit=limit, offset=offset)


# Аудит


@router.get("/audit", summary="Журнал аудита")
async def audit_log(
    admin: SuperUser,
    db: DbDep,
    action: str = Query(default="", max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> Page[AuditEntryOut]:
    from app.db.models import AuditLog

    base = select(AuditLog)
    if action:
        base = base.where(AuditLog.action == action)
    total = int(await db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = list(
        (
            await db.scalars(base.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset))
        ).all()
    )
    return Page[AuditEntryOut](
        items=[AuditEntryOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
