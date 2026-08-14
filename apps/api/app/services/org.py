"""Организации: мультитенантность, участники, приглашения.

Инварианты, которые сервис держит жёстко:

* У организации всегда есть хотя бы один владелец. Понизить или удалить
  последнего нельзя - иначе организация становится неуправляемой и чинить
  это придётся руками в psql.
* Роль нельзя поднять выше собственной. Администратор не может назначить
  кого-то владельцем: это тривиальный путь эскалации привилегий.
* Приглашение - одноразовый токен с сроком жизни, в БД лежит только хеш.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.logging import get_logger
from app.core.security import generate_token, hash_ip, hash_token
from app.db.models import Invitation, Organization, OrgMember, OrgRole, Review, ReviewStatus, User
from app.services import audit
from app.services.mailer import Mailer, invitation_letter

log = get_logger("org")

INVITATION_TTL = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(UTC)


class OrgService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        mailer: Mailer,
        *,
        ip: str = "",
    ) -> None:
        self.db = session
        self.settings = settings
        self.mailer = mailer
        self.ip_hash = hash_ip(ip) if ip else ""

    # Организации

    async def create(
        self, owner: User, *, name: str, slug: str, description: str = ""
    ) -> Organization:
        org = Organization(name=name, slug=slug, description=description)
        self.db.add(org)
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError(
                f"Слаг «{slug}» уже занят", details={"fields": {"slug": "уже занят"}}
            ) from None

        self.db.add(OrgMember(org_id=org.id, user_id=owner.id, role=OrgRole.OWNER.value))
        await audit.record(
            self.db,
            audit.Action.ORG_CREATED,
            actor_user_id=owner.id,
            org_id=org.id,
            target_type="org",
            target_id=str(org.id),
            ip_hash=self.ip_hash,
            meta={"slug": slug},
        )
        await self.db.flush()
        return org

    async def get_by_slug(self, slug: str) -> Organization:
        org = await self.db.scalar(select(Organization).where(Organization.slug == slug))
        if org is None:
            raise NotFoundError("Организация не найдена")
        return org

    async def get_membership(self, org_id: uuid.UUID, user_id: uuid.UUID) -> OrgMember | None:
        member = await self.db.scalar(
            select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == user_id)
        )
        return member

    async def _lock_org(self, org_id: uuid.UUID) -> Organization:
        """Сериализует изменения состава конкретной организации.

        Проверка «остался хотя бы один owner» является инвариантом над
        несколькими строками. Без блокировки два владельца могут одновременно
        удалить/понизить друг друга, и оба увидят старое количество владельцев.
        """
        org = await self.db.scalar(
            select(Organization).where(Organization.id == org_id).with_for_update()
        )
        if org is None:
            raise NotFoundError("Организация не найдена")
        return org

    async def _locked_member_role(self, org_id: uuid.UUID, user_id: uuid.UUID) -> OrgRole:
        """Возвращает актуальную роль после блокировки организации.

        Контекст FastAPI строится до ожидания row lock. Пока запрос ждёт,
        пользователя могут исключить или понизить; повторная проверка не даёт
        старому контексту выполнить привилегированное действие после этого.
        """
        member = await self.get_membership(org_id, user_id)
        if member is None:
            raise PermissionDeniedError("Вы больше не состоите в этой организации")
        return OrgRole(member.role)

    async def list_for_user(self, user: User) -> list[dict[str, object]]:
        """Организации пользователя со счётчиками для дашборда."""
        members_count = (
            select(func.count())
            .select_from(OrgMember)
            .where(OrgMember.org_id == Organization.id)
            .scalar_subquery()
        )
        pending_count = (
            select(func.count())
            .select_from(Review)
            .where(
                Review.org_id == Organization.id,
                Review.status == ReviewStatus.PENDING.value,
            )
            .scalar_subquery()
        )

        stmt: Select[Any] = (
            select(Organization, OrgMember.role, members_count, pending_count)
            .join(OrgMember, OrgMember.org_id == Organization.id)
            .where(OrgMember.user_id == user.id)
            .order_by(Organization.created_at)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "id": org.id,
                "slug": org.slug,
                "name": org.name,
                "plan": org.plan,
                "role": role,
                "members_count": members,
                "pending_reviews": pending,
            }
            for org, role, members, pending in rows
        ]

    async def update(
        self, org: Organization, actor: User, changes: dict[str, object]
    ) -> Organization:
        org = await self._lock_org(org.id)
        if (await self._locked_member_role(org.id, actor.id)).level < OrgRole.ADMIN.level:
            raise PermissionDeniedError("Недостаточно прав для изменения организации")

        applied: dict[str, object] = {}
        for field in ("name", "description", "is_public", "auto_approve_reviews"):
            value = changes.get(field)
            if value is not None and getattr(org, field) != value:
                setattr(org, field, value)
                applied[field] = value

        if applied:
            await audit.record(
                self.db,
                audit.Action.ORG_UPDATED,
                actor_user_id=actor.id,
                org_id=org.id,
                target_type="org",
                target_id=str(org.id),
                ip_hash=self.ip_hash,
                meta={"changed": applied},
            )
        return org

    # Участники

    async def list_members(self, org: Organization) -> list[dict[str, object]]:
        rows = (
            await self.db.execute(
                select(User, OrgMember)
                .join(OrgMember, OrgMember.user_id == User.id)
                .where(OrgMember.org_id == org.id)
                .order_by(OrgMember.created_at)
            )
        ).all()
        return [
            {
                "user_id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": member.role,
                "joined_at": member.created_at,
            }
            for user, member in rows
        ]

    async def _count_owners(self, org_id: uuid.UUID) -> int:
        return int(
            await self.db.scalar(
                select(func.count())
                .select_from(OrgMember)
                .where(OrgMember.org_id == org_id, OrgMember.role == OrgRole.OWNER.value)
            )
            or 0
        )

    async def change_role(
        self,
        org: Organization,
        actor: User,
        actor_role: OrgRole,
        target_user_id: uuid.UUID,
        new_role: OrgRole,
    ) -> OrgMember:
        org = await self._lock_org(org.id)
        actor_role = await self._locked_member_role(org.id, actor.id)
        member = await self.get_membership(org.id, target_user_id)
        if member is None:
            raise NotFoundError("Участник не найден в этой организации")

        # Нельзя выдать роль выше собственной - иначе admin делает себя owner.
        if new_role.level > actor_role.level:
            raise PermissionDeniedError("Нельзя назначить роль выше собственной")
        # И нельзя трогать того, кто выше тебя.
        if OrgRole(member.role).level > actor_role.level:
            raise PermissionDeniedError("Недостаточно прав для изменения этого участника")

        if (
            member.role == OrgRole.OWNER.value
            and new_role != OrgRole.OWNER
            and await self._count_owners(org.id) <= 1
        ):
            raise ConflictError(
                "Это единственный владелец организации. " "Сначала назначьте второго владельца."
            )

        previous = member.role
        member.role = new_role.value
        await audit.record(
            self.db,
            audit.Action.MEMBER_ROLE_CHANGED,
            actor_user_id=actor.id,
            org_id=org.id,
            target_type="user",
            target_id=str(target_user_id),
            ip_hash=self.ip_hash,
            meta={"from": previous, "to": new_role.value},
        )
        return member

    async def remove_member(
        self,
        org: Organization,
        actor: User,
        actor_role: OrgRole,
        target_user_id: uuid.UUID,
    ) -> None:
        org = await self._lock_org(org.id)
        actor_role = await self._locked_member_role(org.id, actor.id)
        member = await self.get_membership(org.id, target_user_id)
        if member is None:
            raise NotFoundError("Участник не найден в этой организации")

        # Выйти самому можно всегда; исключить другого - только при старшинстве.
        if target_user_id != actor.id and OrgRole(member.role).level >= actor_role.level:
            raise PermissionDeniedError("Недостаточно прав для исключения этого участника")

        if member.role == OrgRole.OWNER.value and await self._count_owners(org.id) <= 1:
            raise ConflictError(
                "Нельзя исключить единственного владельца. " "Передайте владение другому участнику."
            )

        await self.db.delete(member)
        await audit.record(
            self.db,
            audit.Action.MEMBER_REMOVED,
            actor_user_id=actor.id,
            org_id=org.id,
            target_type="user",
            target_id=str(target_user_id),
            ip_hash=self.ip_hash,
            meta={"self": target_user_id == actor.id},
        )

    # Приглашения

    async def invite(
        self,
        org: Organization,
        actor: User,
        actor_role: OrgRole,
        *,
        email: str,
        role: OrgRole,
    ) -> Invitation:
        org = await self._lock_org(org.id)
        actor_role = await self._locked_member_role(org.id, actor.id)
        if role.level > actor_role.level:
            raise PermissionDeniedError("Нельзя пригласить с ролью выше собственной")

        existing_user = await self.db.scalar(select(User).where(User.email == email))
        if existing_user is not None:
            member = await self.get_membership(org.id, existing_user.id)
            if member is not None:
                raise ConflictError("Этот пользователь уже состоит в организации")

        # Незакрытое приглашение на тот же адрес перевыпускаем, а не плодим:
        # частичный уникальный индекс всё равно не даст создать второе.
        pending = await self.db.scalar(
            select(Invitation).where(
                Invitation.org_id == org.id,
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
            )
        )
        raw, token_hash = generate_token()
        if pending is not None:
            pending.token_hash = token_hash
            pending.role = role.value
            pending.expires_at = _now() + INVITATION_TTL
            invitation = pending
        else:
            invitation = Invitation(
                org_id=org.id,
                email=email,
                role=role.value,
                token_hash=token_hash,
                invited_by=actor.id,
                expires_at=_now() + INVITATION_TTL,
            )
            self.db.add(invitation)
        await self.db.flush()

        await audit.record(
            self.db,
            audit.Action.INVITE_SENT,
            actor_user_id=actor.id,
            org_id=org.id,
            target_type="invitation",
            target_id=str(invitation.id),
            ip_hash=self.ip_hash,
            meta={"role": role.value},
        )
        await self.mailer.enqueue(
            self.db,
            invitation_letter(self.settings, email, raw, org.name, actor.full_name or actor.email),
            dedupe_key=f"invitation:{org.id}:{email}",
        )
        return invitation

    async def list_invitations(self, org: Organization) -> list[Invitation]:
        return list(
            (
                await self.db.scalars(
                    select(Invitation)
                    .where(Invitation.org_id == org.id, Invitation.accepted_at.is_(None))
                    .order_by(Invitation.created_at.desc())
                )
            ).all()
        )

    async def revoke_invitation(
        self, org: Organization, actor: User, invitation_id: uuid.UUID
    ) -> None:
        org = await self._lock_org(org.id)
        if (await self._locked_member_role(org.id, actor.id)).level < OrgRole.ADMIN.level:
            raise PermissionDeniedError("Недостаточно прав для отзыва приглашения")
        invitation = await self.db.scalar(
            select(Invitation)
            .where(
                Invitation.id == invitation_id,
                Invitation.org_id == org.id,
                Invitation.accepted_at.is_(None),
            )
            .with_for_update()
        )
        if invitation is None:
            raise NotFoundError("Приглашение не найдено")
        await self.db.delete(invitation)
        await audit.record(
            self.db,
            audit.Action.INVITE_REVOKED,
            actor_user_id=actor.id,
            org_id=org.id,
            target_type="invitation",
            target_id=str(invitation_id),
            ip_hash=self.ip_hash,
        )

    async def accept_invitation(self, user: User, token: str) -> Organization:
        invitation = await self.db.scalar(
            select(Invitation)
            .where(
                Invitation.token_hash == hash_token(token),
                Invitation.accepted_at.is_(None),
                Invitation.expires_at > _now(),
            )
            # Приглашение адресное и одноразовое: конкурентный accept должен
            # дождаться первой транзакции, а затем увидеть accepted_at.
            .with_for_update()
        )
        if invitation is None:
            raise ValidationError(
                "Приглашение недействительно или устарело", code="invalid_invitation"
            )

        # Приглашение адресное: принять его может только владелец адреса.
        # Иначе перехваченная ссылка даёт доступ к чужой организации.
        if invitation.email != user.email:
            log.warning(
                "invitation_email_mismatch",
                invitation_id=str(invitation.id),
                user_id=str(user.id),
            )
            raise PermissionDeniedError(
                "Приглашение выписано на другой адрес. "
                "Войдите под учётной записью с этим адресом."
            )

        org = await self.db.get(Organization, invitation.org_id)
        if org is None:
            raise NotFoundError("Организация больше не существует")

        existing = await self.get_membership(org.id, user.id)
        if existing is None:
            self.db.add(OrgMember(org_id=org.id, user_id=user.id, role=invitation.role))
        invitation.accepted_at = _now()

        await audit.record(
            self.db,
            audit.Action.INVITE_ACCEPTED,
            actor_user_id=user.id,
            org_id=org.id,
            target_type="invitation",
            target_id=str(invitation.id),
            ip_hash=self.ip_hash,
        )
        return org
