"""Журнал аудита.

Записи добавляются в ТУ ЖЕ сессию БД, что и само действие, и коммитятся
вместе с ним. Отдельный коммит для аудита означал бы, что при откате
основной операции в журнале остаётся след несуществующего изменения -
и наоборот.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


class Action:
    """Словарь действий. Строки-литералы по коду разъезжаются, константы - нет."""

    # аутентификация
    LOGIN_SUCCESS = "auth.login.success"
    LOGIN_FAILED = "auth.login.failed"
    LOGIN_LOCKED = "auth.login.locked"
    LOGOUT = "auth.logout"
    REGISTER = "auth.register"
    EMAIL_VERIFIED = "auth.email.verified"
    PASSWORD_CHANGED = "auth.password.changed"  # noqa: S105 - audit event name
    PASSWORD_RESET_REQUESTED = "auth.password.reset_requested"  # noqa: S105 - audit event name
    PASSWORD_RESET_COMPLETED = "auth.password.reset_completed"  # noqa: S105 - audit event name
    SESSION_REVOKED = "auth.session.revoked"
    TOTP_ENABLED = "auth.totp.enabled"
    TOTP_DISABLED = "auth.totp.disabled"
    RECOVERY_CODE_USED = "auth.recovery_code.used"

    # организации
    ORG_CREATED = "org.created"
    ORG_UPDATED = "org.updated"
    MEMBER_ROLE_CHANGED = "org.member.role_changed"
    MEMBER_REMOVED = "org.member.removed"
    INVITE_SENT = "org.invite.sent"
    INVITE_ACCEPTED = "org.invite.accepted"
    INVITE_REVOKED = "org.invite.revoked"

    # отзывы
    REVIEW_SUBMITTED = "review.submitted"
    REVIEW_MODERATED = "review.moderated"

    # администрирование
    USER_DEACTIVATED = "admin.user.deactivated"
    USER_ACTIVATED = "admin.user.activated"


async def record(
    session: AsyncSession,
    action: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    target_type: str = "",
    target_id: str = "",
    ip_hash: str = "",
    meta: dict[str, Any] | None = None,
) -> None:
    """Добавляет запись в журнал. Коммит - за вызывающим кодом."""
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            org_id=org_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id)[:64],
            ip_hash=ip_hash,
            meta=meta or {},
        )
    )
