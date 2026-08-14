"""Схема данных.

Общие решения по схеме:

* Роли и статусы хранятся как ``String`` с ``CHECK``-ограничением, а не как
  native ENUM PostgreSQL. Добавить значение в CHECK - одна строка миграции;
  изменить native ENUM - блокирующая операция с пересозданием типа.
* Каждая таблица, где есть ``org_id``, изолируется по нему на уровне запроса.
  Функции доступа собраны в ``app.api.deps`` - обращаться к таблицам
  арендаторов в обход них нельзя.
* Токены (сессии, приглашения, сброс пароля) хранятся только в виде хеша.
* Удаление пользователя каскадно уносит его сессии и токены, но НЕ уносит
  отзывы и записи аудита: они должны пережить удаление автора.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk

# Перечисления


class OrgRole(StrEnum):
    """Роль внутри организации. Порядок значений = порядок возрастания прав."""

    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"

    @property
    def level(self) -> int:
        return {"member": 1, "admin": 2, "owner": 3}[self.value]


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SPAM = "spam"


class TokenPurpose(StrEnum):
    EMAIL_VERIFY = "email_verify"
    PASSWORD_RESET = "password_reset"  # noqa: S105 - semantic token purpose, not a secret


# Пользователи и аутентификация


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()

    # E-mail всегда хранится в нижнем регистре - нормализация выполняется в
    # схемах ввода. Уникальный индекс тогда работает как ожидается, без citext.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(
        String(120), default="", server_default=text("''"), nullable=False
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Секрет TOTP лежит зашифрованным (AES-GCM, ключ выведен из SECRET_KEY).
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # Последний принятый 30-секундный шаг. Не даёт повторно использовать TOTP.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)

    # Счётчик неудачных входов и блокировка. Держим в БД, а не в Redis:
    # защита от перебора не должна отключаться вместе с кэшем.
    failed_login_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Обнуляет все выданные сессии при смене пароля (см. AuthService).
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    memberships: Mapped[list[OrgMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("position('@' in email) > 1", name="email_shape"),
        Index(
            "ix_users_locked_until",
            "locked_until",
            postgresql_where=text("locked_until IS NOT NULL"),
        ),
    )

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None


class Session(Base):
    """Серверная сессия. Клиент получает только непредсказуемый токен."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    ip_hash: Mapped[str] = mapped_column(
        String(32), default="", server_default=text("''"), nullable=False
    )
    user_agent: Mapped[str] = mapped_column(
        String(256), default="", server_default=text("''"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (
        # Частый запрос «действующие сессии пользователя» - покрываем индексом.
        Index("ix_sessions_user_active", "user_id", "expires_at"),
    )


class EmailToken(Base):
    """Одноразовый токен: подтверждение почты и сброс пароля."""

    __tablename__ = "email_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("purpose IN ('email_verify', 'password_reset')", name="purpose_known"),
        Index("ix_email_tokens_user_purpose", "user_id", "purpose"),
    )


class RecoveryCode(Base):
    """Резервный код на случай потери устройства с TOTP."""

    __tablename__ = "recovery_codes"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (UniqueConstraint("user_id", "code_hash", name="uq_recovery_user_code"),)


class MailOutbox(Base):
    """Письмо, поставленное в очередь в транзакции бизнес-операции."""

    __tablename__ = "mail_outbox"

    id: Mapped[uuid.UUID] = uuid_pk()
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_mail_outbox_pending",
            "available_at",
            "created_at",
            postgresql_where=text("sent_at IS NULL AND failed_at IS NULL"),
        ),
        Index("ix_mail_outbox_dedupe", "dedupe_key"),
    )


# Мультитенантность


class Organization(Base, TimestampMixin):
    """Арендатор. Единица изоляции данных и владелец публичной страницы."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, default="", server_default=text("''"), nullable=False
    )

    plan: Mapped[str] = mapped_column(
        String(32), default="free", server_default=text("'free'"), nullable=False
    )
    # Показывать ли публичную страницу с отзывами.
    is_public: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    # Пропускать ли отзывы в публикацию без ручной модерации.
    auto_approve_reviews: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )

    members: Mapped[list[OrgMember]] = relationship(
        back_populates="organization", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # slug попадает в URL: только строчные буквы, цифры и дефис.
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'", name="slug_shape"),
        CheckConstraint("plan IN ('free', 'team', 'business')", name="plan_known"),
    )


class OrgMember(Base):
    """Членство пользователя в организации с ролью."""

    __tablename__ = "org_members"

    org_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(
        String(16),
        default=OrgRole.MEMBER.value,
        server_default=text("'member'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")

    __table_args__ = (
        CheckConstraint("role IN ('member', 'admin', 'owner')", name="role_known"),
        Index("ix_org_members_user", "user_id"),
    )


class Invitation(Base):
    """Приглашение в организацию по e-mail."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(
        String(16),
        default=OrgRole.MEMBER.value,
        server_default=text("'member'"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    organization: Mapped[Organization] = relationship()

    __table_args__ = (
        CheckConstraint("role IN ('member', 'admin', 'owner')", name="role_known"),
        # Одно активное приглашение на адрес в организации.
        Index(
            "uq_invitations_pending",
            "org_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL"),
        ),
    )


# Отзывы (публичный контур)


class Review(Base):
    """Отзыв. Приходит из публичной формы, публикуется после модерации."""

    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    author_name: Mapped[str] = mapped_column(String(80), nullable=False)
    # E-mail автора не показывается публично - только модератору.
    author_email: Mapped[str] = mapped_column(
        String(320), default="", server_default=text("''"), nullable=False
    )
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(
        String(120), default="", server_default=text("''"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16),
        default=ReviewStatus.PENDING.value,
        server_default=text("'pending'"),
        nullable=False,
    )
    # Сырой IP не храним - только HMAC. Достаточно для антиспама,
    # безопасно при утечке дампа.
    ip_hash: Mapped[str] = mapped_column(
        String(32), default="", server_default=text("''"), nullable=False
    )
    user_agent: Mapped[str] = mapped_column(
        String(256), default="", server_default=text("''"), nullable=False
    )

    moderated_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderation_note: Mapped[str] = mapped_column(
        String(500), default="", server_default=text("''"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    organization: Mapped[Organization] = relationship()

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'spam')", name="status_known"
        ),
        CheckConstraint("length(body) BETWEEN 10 AND 5000", name="body_length"),
        # Главный публичный запрос: одобренные отзывы организации, новые сверху.
        Index("ix_reviews_public", "org_id", "status", text("created_at DESC")),
        # Очередь модерации.
        Index(
            "ix_reviews_pending",
            "org_id",
            text("created_at DESC"),
            postgresql_where=text("status = 'pending'"),
        ),
        # Антиспам: сколько отзывов пришло с этого адреса за последнее время.
        Index("ix_reviews_ip_recent", "ip_hash", text("created_at DESC")),
    )


# Аудит


class AuditLog(Base):
    """Журнал значимых действий.

    Пишется в ту же транзакцию, что и само действие: либо есть и изменение,
    и запись о нём, либо нет ни того, ни другого. Расследовать инцидент по
    журналу, который может расходиться с данными, невозможно.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    # ON DELETE SET NULL: удаление пользователя не стирает историю его действий.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(
        String(32), default="", server_default=text("''"), nullable=False
    )
    target_id: Mapped[str] = mapped_column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    ip_hash: Mapped[str] = mapped_column(
        String(32), default="", server_default=text("''"), nullable=False
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_actor_time", "actor_user_id", text("created_at DESC")),
        Index("ix_audit_org_time", "org_id", text("created_at DESC")),
        Index("ix_audit_action_time", "action", text("created_at DESC")),
    )
