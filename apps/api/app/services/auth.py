"""Аутентификация и управление учётными записями.

Свойства, которые здесь обеспечиваются, и цена отказа от каждого:

* Нет перечисления пользователей. Регистрация, вход и восстановление
  пароля отвечают одинаково независимо от того, существует адрес или нет.
  Иначе форма входа превращается в бесплатный сервис проверки «есть ли
  у вас аккаунт», а это готовый список целей для фишинга.
* Постоянное время отклика. Если пользователя нет, всё равно
  выполняется холостая проверка пароля: разница во времени ответа выдаёт
  существование записи не хуже разницы в тексте.
* Смена пароля завершает все сеансы. Украденная сессия перестаёт
  работать сразу после того, как владелец сменил пароль.
* Блокировка после серии неудач хранится в БД, а не в кэше: защита от
  перебора не должна исчезать вместе с Redis.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    AccountLockedError,
    AppError,
    AuthenticationError,
    ConflictError,
    InvalidCredentialsError,
    TwoFactorRequiredError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import (
    check_password_policy,
    decrypt_totp_secret,
    dummy_verify_async,
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_token,
    generate_totp_secret,
    hash_ip,
    hash_password_async,
    hash_recovery_code,
    hash_token,
    match_totp_step,
    password_needs_rehash,
    recovery_code_hash_candidates,
    totp_provisioning_uri,
    verify_password_async,
)
from app.db.models import EmailToken, RecoveryCode, Session, TokenPurpose, User
from app.services import audit
from app.services.mailer import (
    Mailer,
    password_changed_letter,
    password_reset_letter,
    verification_letter,
)

log = get_logger("auth")

EMAIL_VERIFY_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)
# Реже, чем раз в минуту, обновлять last_seen_at смысла нет: это была бы
# запись в БД на каждый запрос ради поля, точность которого никому не нужна.
SESSION_TOUCH_INTERVAL = timedelta(minutes=1)


def _now() -> datetime:
    return datetime.now(UTC)


class EmailNotVerifiedError(AuthenticationError):
    code = "email_not_verified"
    message = "Адрес не подтверждён. Проверьте почту."


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        mailer: Mailer,
        *,
        ip: str = "",
        user_agent: str = "",
    ) -> None:
        self.db = session
        self.settings = settings
        self.mailer = mailer
        self.ip = ip
        self.ip_hash = hash_ip(ip) if ip else ""
        self.user_agent = user_agent[:256]

    async def _lock_user(self, user: User) -> User:
        locked = await self.db.scalar(select(User).where(User.id == user.id).with_for_update())
        if locked is None:
            raise AuthenticationError("Учётная запись больше не существует", status_code=401)
        return locked

    # Регистрация

    async def register(self, email: str, password: str, full_name: str = "") -> User | None:
        """Создаёт пользователя.

        Возвращает ``None``, если адрес уже занят. Вызывающий код обязан
        отдать клиенту тот же ответ, что и при успехе, - см. роут.
        """
        if not self.settings.allow_public_signup:
            raise AppError(
                "Публичная регистрация отключена. Доступ выдаётся по приглашению.",
                code="signup_disabled",
                status_code=403,
            )

        policy = check_password_policy(password, email=email)
        if not policy.ok:
            raise ValidationError(policy.reason, details={"fields": {"password": policy.reason}})

        existing = await self.db.scalar(select(User).where(User.email == email))
        if existing is not None:
            # Успешная регистрация тратит Argon2 hash; duplicate делает такую же
            # дорогую операцию, чтобы время ответа не подтверждало наличие адреса.
            await dummy_verify_async()
            log.info("register_duplicate_email")
            return None

        user = User(
            email=email,
            password_hash=await hash_password_async(password),
            full_name=full_name,
            is_active=True,
            # Если подтверждение почты не требуется, считаем адрес
            # подтверждённым сразу - иначе поле навсегда останется пустым
            # и логика «подтверждён ли» начнёт врать.
            email_verified_at=None if self.settings.require_email_verification else _now(),
        )
        self.db.add(user)

        try:
            await self.db.flush()
        except IntegrityError:
            # Гонка: два запроса с одним адресом пришли одновременно.
            # Уникальный индекс - последний рубеж, и он сработал.
            await self.db.rollback()
            return None

        await audit.record(
            self.db,
            audit.Action.REGISTER,
            actor_user_id=user.id,
            target_type="user",
            target_id=str(user.id),
            ip_hash=self.ip_hash,
        )
        return user

    async def send_verification(self, user: User) -> None:
        if user.is_verified:
            return
        raw = await self._issue_email_token(user, TokenPurpose.EMAIL_VERIFY, EMAIL_VERIFY_TTL)
        await self.mailer.enqueue(
            self.db,
            verification_letter(self.settings, user.email, raw),
            dedupe_key=f"email-verify:{user.id}",
        )

    async def verify_email(self, token: str) -> User:
        user = await self._consume_email_token(token, TokenPurpose.EMAIL_VERIFY)
        if user.email_verified_at is None:
            user.email_verified_at = _now()
        await audit.record(
            self.db,
            audit.Action.EMAIL_VERIFIED,
            actor_user_id=user.id,
            target_type="user",
            target_id=str(user.id),
            ip_hash=self.ip_hash,
        )
        return user

    # Вход

    async def login(self, email: str, password: str, totp_code: str = "") -> User:
        user = await self.db.scalar(select(User).where(User.email == email).with_for_update())

        if user is None:
            # Холостая проверка: время ответа не должно зависеть от того,
            # есть такой адрес или нет.
            await dummy_verify_async()
            await self._record_failure(None)
            raise InvalidCredentialsError

        if user.locked_until is not None and user.locked_until > _now():
            remaining = int((user.locked_until - _now()).total_seconds())
            await audit.record(
                self.db,
                audit.Action.LOGIN_LOCKED,
                actor_user_id=user.id,
                ip_hash=self.ip_hash,
            )
            raise AccountLockedError(
                f"Слишком много неудачных попыток. Повторите через {remaining // 60 + 1} мин.",
                details={"retry_after_s": remaining},
            )

        if not await verify_password_async(user.password_hash, password):
            await self._register_failed_attempt(user)
            raise InvalidCredentialsError

        if not user.is_active:
            # Отдельный код: пользователь ввёл верный пароль, проблема не в нём.
            raise AuthenticationError(
                "Учётная запись отключена. Обратитесь к администратору.",
                code="account_disabled",
                status_code=403,
            )

        if self.settings.require_email_verification and not user.is_verified:
            raise EmailNotVerifiedError

        if user.totp_enabled:
            if not totp_code:
                raise TwoFactorRequiredError
            if not await self._check_second_factor(user, totp_code):
                await self._register_failed_attempt(user)
                raise AuthenticationError(
                    "Неверный код подтверждения", code="invalid_totp", status_code=401
                )

        # Пароль верен: если хеш сделан устаревшими параметрами - обновляем.
        # Это единственный момент, когда открытый пароль доступен.
        if password_needs_rehash(user.password_hash):
            user.password_hash = await hash_password_async(password)
            log.info("password_rehashed", user_id=str(user.id))

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = _now()

        await audit.record(
            self.db,
            audit.Action.LOGIN_SUCCESS,
            actor_user_id=user.id,
            ip_hash=self.ip_hash,
            meta={"totp": user.totp_enabled},
        )
        return user

    async def reauthenticate(self, user: User, password: str, totp_code: str = "") -> None:
        """Повторная проверка личности для чувствительных операций.

        Требуется там, где одной действующей сессии мало: перевыпуск
        резервных кодов 2FA, смена критичных настроек. Угнанная сессия
        (даже с валидным CSRF-токеном) без знания пароля и второго фактора
        такую операцию выполнить не сможет.

        В отличие от входа, TOTP здесь проверяется НАПРЯМУЮ и резервные коды
        НЕ гасятся: перевыпуск кодов не должен сжигать один из старых.
        """
        user = await self._lock_user(user)
        if not await verify_password_async(user.password_hash, password):
            raise InvalidCredentialsError("Пароль указан неверно")
        if user.totp_enabled and not self._accept_totp(user, totp_code):
            raise AuthenticationError(
                "Неверный код двухфакторной аутентификации",
                code="invalid_totp",
                status_code=401,
            )

    @staticmethod
    def _accept_totp(user: User, code: str) -> bool:
        if not user.totp_secret_enc:
            return False
        secret = decrypt_totp_secret(user.totp_secret_enc, str(user.id))
        if not secret:
            return False
        step = match_totp_step(secret, code)
        if step is None or (user.totp_last_step is not None and step <= user.totp_last_step):
            return False
        user.totp_last_step = step
        return True

    async def _check_second_factor(self, user: User, code: str) -> bool:
        """Проверяет TOTP, а если не подошёл - резервный код."""
        if self._accept_totp(user, code):
            return True

        code_hashes = recovery_code_hash_candidates(code)
        recovery = await self.db.scalar(
            select(RecoveryCode)
            .where(
                RecoveryCode.user_id == user.id,
                RecoveryCode.code_hash.in_(code_hashes),
                RecoveryCode.used_at.is_(None),
            )
            # Один recovery-код не должен выдать две сессии при двух
            # параллельных запросах: второй ждёт блокировку и затем уже
            # не находит строку с used_at IS NULL.
            .with_for_update()
        )
        if recovery is not None:
            recovery.used_at = _now()  # код одноразовый
            await audit.record(
                self.db,
                audit.Action.RECOVERY_CODE_USED,
                actor_user_id=user.id,
                ip_hash=self.ip_hash,
            )
            log.warning("recovery_code_used", user_id=str(user.id))
            return True
        return False

    async def _register_failed_attempt(self, user: User) -> None:
        user.failed_login_count += 1
        if user.failed_login_count >= self.settings.login_max_failures:
            user.locked_until = _now() + timedelta(minutes=self.settings.login_lockout_minutes)
            user.failed_login_count = 0
            log.warning(
                "account_locked",
                user_id=str(user.id),
                minutes=self.settings.login_lockout_minutes,
            )
        await self._record_failure(user)

    async def _record_failure(self, user: User | None) -> None:
        await audit.record(
            self.db,
            audit.Action.LOGIN_FAILED,
            actor_user_id=user.id if user else None,
            ip_hash=self.ip_hash,
        )

    # Сессии

    async def create_session(self, user: User) -> tuple[str, Session]:
        """Создаёт сессию и возвращает (открытый токен, объект сессии)."""
        raw_token, token_hash = generate_token()
        now = _now()
        session = Session(
            user_id=user.id,
            token_hash=token_hash,
            ip_hash=self.ip_hash,
            user_agent=self.user_agent,
            expires_at=now + timedelta(hours=self.settings.session_absolute_ttl_h),
        )
        self.db.add(session)
        await self.db.flush()
        await self._enforce_session_limit(user, keep_id=session.id)
        return raw_token, session

    async def _enforce_session_limit(self, user: User, *, keep_id: uuid.UUID) -> None:
        """Вытесняет самые старые сессии сверх лимита.

        Без этого угнанный токен можно бесконечно продлевать, создавая новые
        сессии, и список активных устройств у пользователя становится
        нечитаемым - а значит, бесполезным для обнаружения взлома.
        """
        limit = self.settings.session_max_per_user
        now = _now()
        active_ids = (
            await self.db.scalars(
                select(Session.id)
                .where(
                    Session.user_id == user.id,
                    Session.revoked_at.is_(None),
                    Session.expires_at > now,
                )
                .order_by(Session.last_seen_at.desc())
            )
        ).all()

        excess = [sid for sid in active_ids[limit:] if sid != keep_id]
        if excess:
            await self.db.execute(
                update(Session).where(Session.id.in_(excess)).values(revoked_at=now)
            )
            log.info("sessions_evicted", user_id=str(user.id), count=len(excess))

    async def resolve_session(self, raw_token: str) -> tuple[User, Session] | None:
        """Проверяет токен из cookie и возвращает пользователя с сессией."""
        if not raw_token or len(raw_token) > 256:
            return None

        now = _now()
        row = (
            await self.db.execute(
                select(Session, User)
                .join(User, User.id == Session.user_id)
                .where(
                    Session.token_hash == hash_token(raw_token),
                    Session.revoked_at.is_(None),
                    Session.expires_at > now,
                )
            )
        ).first()
        if row is None:
            return None

        session_obj, user = row

        if not user.is_active:
            await self.revoke_session(session_obj, reason="user_inactive")
            return None

        # Простой без активности.
        idle_deadline = session_obj.last_seen_at + timedelta(hours=self.settings.session_idle_ttl_h)
        if idle_deadline < now:
            await self.revoke_session(session_obj, reason="idle_timeout")
            return None

        # Смена пароля аннулирует все сессии, выданные до неё.
        if (
            user.password_changed_at is not None
            and session_obj.created_at < user.password_changed_at
        ):
            await self.revoke_session(session_obj, reason="password_changed")
            return None

        if now - session_obj.last_seen_at > SESSION_TOUCH_INTERVAL:
            session_obj.last_seen_at = now

        return user, session_obj

    async def revoke_session(self, session_obj: Session, *, reason: str = "logout") -> None:
        if session_obj.revoked_at is None:
            session_obj.revoked_at = _now()
            await audit.record(
                self.db,
                audit.Action.SESSION_REVOKED,
                actor_user_id=session_obj.user_id,
                target_type="session",
                target_id=str(session_obj.id),
                ip_hash=self.ip_hash,
                meta={"reason": reason},
            )

    async def revoke_all_sessions(self, user: User, *, except_id: uuid.UUID | None = None) -> int:
        stmt = update(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None))
        if except_id is not None:
            stmt = stmt.where(Session.id != except_id)
        result = await self.db.execute(stmt.values(revoked_at=_now()))
        return int(cast(CursorResult[object], result).rowcount or 0)

    async def list_sessions(self, user: User) -> list[Session]:
        now = _now()
        return list(
            (
                await self.db.scalars(
                    select(Session)
                    .where(
                        Session.user_id == user.id,
                        Session.revoked_at.is_(None),
                        Session.expires_at > now,
                    )
                    .order_by(Session.last_seen_at.desc())
                )
            ).all()
        )

    # Пароль

    async def change_password(self, user: User, current: str, new: str) -> None:
        user = await self._lock_user(user)
        if not await verify_password_async(user.password_hash, current):
            raise InvalidCredentialsError("Текущий пароль указан неверно")

        policy = check_password_policy(new, email=user.email)
        if not policy.ok:
            raise ValidationError(
                policy.reason, details={"fields": {"new_password": policy.reason}}
            )
        if await verify_password_async(user.password_hash, new):
            raise ValidationError("Новый пароль совпадает с текущим")

        user.password_hash = await hash_password_async(new)
        user.password_changed_at = _now()
        await audit.record(
            self.db,
            audit.Action.PASSWORD_CHANGED,
            actor_user_id=user.id,
            ip_hash=self.ip_hash,
        )
        await self.mailer.enqueue(self.db, password_changed_letter(self.settings, user.email))

    async def request_password_reset(self, email: str) -> None:
        """Всегда завершается успешно - независимо от существования адреса."""
        user = await self.db.scalar(select(User).where(User.email == email))
        if user is None or not user.is_active:
            log.info("password_reset_unknown_email")
            return

        raw = await self._issue_email_token(user, TokenPurpose.PASSWORD_RESET, PASSWORD_RESET_TTL)
        await audit.record(
            self.db,
            audit.Action.PASSWORD_RESET_REQUESTED,
            actor_user_id=user.id,
            ip_hash=self.ip_hash,
        )
        await self.mailer.enqueue(
            self.db,
            password_reset_letter(self.settings, user.email, raw),
            dedupe_key=f"password-reset:{user.id}",
        )

    async def reset_password(self, token: str, new_password: str) -> User:
        user = await self._consume_email_token(token, TokenPurpose.PASSWORD_RESET)
        user = await self._lock_user(user)

        policy = check_password_policy(new_password, email=user.email)
        if not policy.ok:
            raise ValidationError(
                policy.reason, details={"fields": {"new_password": policy.reason}}
            )

        user.password_hash = await hash_password_async(new_password)
        user.password_changed_at = _now()
        # Сброс пароля снимает блокировку: владелец доказал доступ к почте.
        user.failed_login_count = 0
        user.locked_until = None
        # Успешный переход по ссылке из письма подтверждает и сам адрес.
        if user.email_verified_at is None:
            user.email_verified_at = _now()

        await self.revoke_all_sessions(user)
        await audit.record(
            self.db,
            audit.Action.PASSWORD_RESET_COMPLETED,
            actor_user_id=user.id,
            ip_hash=self.ip_hash,
        )
        return user

    # Одноразовые токены из писем

    async def _issue_email_token(self, user: User, purpose: TokenPurpose, ttl: timedelta) -> str:
        # Прошлые неиспользованные токены той же цели гасим: активной ссылки
        # должно быть ровно одна, иначе старое письмо остаётся рабочим ключом.
        await self.db.execute(
            update(EmailToken)
            .where(
                EmailToken.user_id == user.id,
                EmailToken.purpose == purpose.value,
                EmailToken.used_at.is_(None),
            )
            .values(used_at=_now())
        )
        raw, token_hash = generate_token()
        self.db.add(
            EmailToken(
                user_id=user.id,
                purpose=purpose.value,
                token_hash=token_hash,
                expires_at=_now() + ttl,
            )
        )
        await self.db.flush()
        return raw

    async def _consume_email_token(self, raw: str, purpose: TokenPurpose) -> User:
        now = _now()
        row = (
            await self.db.execute(
                select(EmailToken, User)
                .join(User, User.id == EmailToken.user_id)
                .where(
                    EmailToken.token_hash == hash_token(raw),
                    EmailToken.purpose == purpose.value,
                    EmailToken.used_at.is_(None),
                    EmailToken.expires_at > now,
                )
                # Токен одноразовый и это должно быть верно не только в
                # последовательном сценарии. Блокируем строку до used_at.
                .with_for_update(of=EmailToken)
            )
        ).first()
        if row is None:
            raise ValidationError(
                "Ссылка недействительна или устарела. Запросите новую.",
                code="invalid_token",
            )
        token_obj, user = row
        token_obj.used_at = now  # одноразовость
        return cast(User, user)

    # Двухфакторная аутентификация

    async def totp_setup(self, user: User) -> tuple[str, str]:
        """Готовит секрет. Он ещё не активен - активирует ``totp_enable``."""
        user = await self._lock_user(user)
        if user.totp_enabled:
            raise ConflictError("Двухфакторная аутентификация уже включена")
        secret = generate_totp_secret()
        user.totp_secret_enc = encrypt_totp_secret(secret, str(user.id))
        uri = totp_provisioning_uri(secret, user.email, self.settings.app_name)
        return secret, uri

    async def totp_enable(self, user: User, code: str) -> list[str]:
        """Включает 2FA после подтверждения кодом. Возвращает резервные коды."""
        user = await self._lock_user(user)
        if user.totp_enabled:
            raise ConflictError("Двухфакторная аутентификация уже включена")
        if not user.totp_secret_enc:
            raise ValidationError("Сначала запросите секрет через /2fa/setup")

        if not self._accept_totp(user, code):
            raise ValidationError("Код не подошёл. Проверьте время на устройстве.")

        user.totp_enabled = True
        codes = await self._replace_recovery_codes(user)
        await audit.record(
            self.db, audit.Action.TOTP_ENABLED, actor_user_id=user.id, ip_hash=self.ip_hash
        )
        return codes

    async def totp_disable(self, user: User, password: str) -> None:
        # Пароль обязателен: иначе украденная сессия отключает 2FA в один клик.
        user = await self._lock_user(user)
        if not await verify_password_async(user.password_hash, password):
            raise InvalidCredentialsError("Пароль указан неверно")
        user.totp_enabled = False
        user.totp_secret_enc = None
        user.totp_last_step = None
        await self.db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
        await audit.record(
            self.db, audit.Action.TOTP_DISABLED, actor_user_id=user.id, ip_hash=self.ip_hash
        )

    async def _replace_recovery_codes(self, user: User) -> list[str]:
        await self.db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
        codes = generate_recovery_codes()
        for code in codes:
            self.db.add(RecoveryCode(user_id=user.id, code_hash=hash_recovery_code(code)))
        await self.db.flush()
        return codes

    async def regenerate_recovery_codes(self, user: User) -> list[str]:
        if not user.totp_enabled:
            raise ConflictError("Двухфакторная аутентификация не включена")
        return await self._replace_recovery_codes(user)

    async def count_unused_recovery_codes(self, user: User) -> int:
        return int(
            await self.db.scalar(
                select(func.count())
                .select_from(RecoveryCode)
                .where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))
            )
            or 0
        )
