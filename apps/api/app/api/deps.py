"""Зависимости FastAPI: доступ к БД, текущий пользователь, CSRF, изоляция арендаторов.

Это единственная дверь к защищённым данным. Правило простое: роут не
обращается к таблицам арендатора напрямую - он получает контекст через
``require_org_role``, который уже проверил членство и роль.

Про транзакции. Зависимость ``get_db`` намеренно НЕ делает commit. Начиная
с FastAPI 0.106 код после ``yield`` выполняется уже ПОСЛЕ отправки ответа:
падение commit'а в этот момент клиент не увидел бы - он бы получил 200 на
операцию, которой не произошло. Поэтому commit делается явно в обработчике,
а зависимость отвечает только за откат и закрытие.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from app.core.logging import get_logger, user_id_var
from app.core.security import constant_time_equals, hash_token, verify_csrf_token
from app.db.models import Organization, OrgMember, OrgRole, User
from app.db.session import get_sessionmaker
from app.net.proxy import ClientInfo
from app.net.ratelimit import RateLimiter
from app.services.auth import AuthService
from app.services.mailer import Mailer
from app.services.org import OrgService

log = get_logger("deps")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


# Инфраструктура


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(settings_dep)]


async def get_db() -> AsyncIterator[AsyncSession]:
    """Сессия БД на время запроса. Commit - за обработчиком."""
    maker = get_sessionmaker()
    session = maker()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


DbDep = Annotated[AsyncSession, Depends(get_db)]


def get_client(request: Request) -> ClientInfo:
    """Реальный клиент, вычисленный RequestContextMiddleware."""
    client = getattr(request.state, "client", None)
    if client is None:  # pragma: no cover - только если middleware отключили
        raise RuntimeError("RequestContextMiddleware не установлен")
    return client  # type: ignore[no-any-return]


ClientDep = Annotated[ClientInfo, Depends(get_client)]


def get_limiter(request: Request) -> RateLimiter:
    return request.app.state.limiter  # type: ignore[no-any-return]


LimiterDep = Annotated[RateLimiter, Depends(get_limiter)]


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer  # type: ignore[no-any-return]


MailerDep = Annotated[Mailer, Depends(get_mailer)]


def get_auth_service(
    request: Request, db: DbDep, settings: SettingsDep, mailer: MailerDep
) -> AuthService:
    client = get_client(request)
    return AuthService(
        db,
        settings,
        mailer,
        ip=client.ip,
        user_agent=request.headers.get("user-agent", ""),
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_org_service(
    request: Request, db: DbDep, settings: SettingsDep, mailer: MailerDep
) -> OrgService:
    return OrgService(db, settings, mailer, ip=get_client(request).ip)


OrgServiceDep = Annotated[OrgService, Depends(get_org_service)]


# Ограничение частоты


def rate_limit(
    bucket: str,
    *,
    limit_of: Callable[[Settings], int],
    window_s: int,
) -> Callable[..., object]:
    """Фабрика зависимости-лимитера.

    ``limit_of`` читает предел из настроек в момент запроса, а не при импорте
    модуля: иначе значение из .env не применилось бы без пересборки образа.
    """

    async def dependency(client: ClientDep, limiter: LimiterDep, settings: SettingsDep) -> None:
        limit = limit_of(settings)
        result = await limiter.consume(f"{bucket}:{client.ip}", limit=limit, window_s=window_s)
        if not result.allowed:
            log.warning("rate_limited", bucket=bucket, retry_after=result.retry_after)
            raise RateLimitError(
                "Слишком много попыток. Повторите позже.",
                headers={"Retry-After": str(result.retry_after)},
                details={"retry_after_s": result.retry_after},
            )

    return dependency


auth_rate_limit = rate_limit("auth", limit_of=lambda s: s.rate_limit_auth_per_min, window_s=60)
review_rate_limit = rate_limit(
    "review", limit_of=lambda s: s.rate_limit_review_per_hour, window_s=3600
)


# Cookie сессии


def set_session_cookies(
    response: Response, settings: Settings, session_token: str, csrf_token: str
) -> None:
    """Ставит cookie сессии и CSRF.

    Сессионная cookie - ``HttpOnly``: JavaScript её не видит, поэтому XSS
    не даёт напрямую украсть токен. CSRF-токен, наоборот, читаемый: SPA
    обязана положить его в заголовок, а браузер сам этого не сделает -
    в этом и состоит защита от межсайтовых запросов.
    """
    max_age = settings.session_absolute_ttl_h * 3600
    response.set_cookie(
        settings.cookie_name,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.secure_cookies,
        # Lax, а не Strict: при Strict переход по ссылке из письма
        # («подтвердите адрес») открывает приложение в состоянии «не вошёл».
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=max_age,
        httponly=False,  # читается фронтендом намеренно
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    for name in (settings.cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(name, path="/", secure=settings.secure_cookies, samesite="lax")


# Аутентификация


async def get_current_user_optional(
    request: Request, auth: AuthServiceDep, settings: SettingsDep
) -> User | None:
    raw = request.cookies.get(settings.cookie_name, "")
    if not raw:
        return None
    resolved = await auth.resolve_session(raw)
    sync_session = auth.db.sync_session
    if sync_session.new or sync_session.dirty or sync_session.deleted:
        # resolve_session обновляет idle timestamp или отзывает протухшую сессию.
        # GET-маршруты сами commit не делают, поэтому обслуживание фиксируем здесь.
        await auth.db.commit()
    if resolved is None:
        return None
    user, session_obj = resolved
    request.state.session = session_obj
    user_id_var.set(str(user.id))
    return user


OptionalUserDep = Annotated[User | None, Depends(get_current_user_optional)]


async def get_current_user(user: OptionalUserDep) -> User:
    if user is None:
        raise AuthenticationError
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_verified_user(user: CurrentUser, settings: SettingsDep) -> User:
    if settings.require_email_verification and not user.is_verified:
        raise AuthenticationError(
            "Подтвердите адрес электронной почты, чтобы продолжить",
            code="email_not_verified",
            status_code=403,
        )
    return user


VerifiedUser = Annotated[User, Depends(get_verified_user)]


async def get_superuser(user: VerifiedUser) -> User:
    if not user.is_superuser:
        # Отдельно логируем: попытка достучаться до админки - событие
        # безопасности, а не рядовая ошибка доступа.
        log.warning("admin_access_denied", user_id=str(user.id))
        raise PermissionDeniedError("Требуются права администратора")
    return user


SuperUser = Annotated[User, Depends(get_superuser)]


# CSRF


async def csrf_protect(request: Request, settings: SettingsDep) -> None:
    """Защита от межсайтовой подделки запроса.

    Применяется ко всем небезопасным методам. Проверок три, и каждая
    закрывает свой класс атак:

    1. Origin/Referer - отсекает запросы с чужого сайта. Заголовок
       Origin браузер ставит сам, подделать его со страницы нельзя.
    2. Совпадение cookie и заголовка - обычный double-submit.
    3. Подпись токена, привязанная к сессии - закрывает случай, когда
       атакующий контролирует поддомен и может выставить свою cookie:
       подписать её нашим ключом он не сможет.
    """
    if request.method in SAFE_METHODS:
        return

    session_raw = request.cookies.get(settings.cookie_name, "")
    if not session_raw:
        # Запрос без сессии не несёт неявных полномочий - подделывать нечего.
        return

    origin = request.headers.get("origin", "")
    if not origin:
        referer = request.headers.get("referer", "")
        parsed = urlsplit(referer)
        if parsed.scheme and parsed.netloc:
            origin = f"{parsed.scheme}://{parsed.netloc}"

    if not origin or origin not in settings.cors_origins:
        log.warning("csrf_origin_rejected", origin=origin or "missing", path=request.url.path)
        raise PermissionDeniedError("Запрос отклонён: недопустимый источник", code="csrf_origin")

    header_token = request.headers.get("x-csrf-token", "")
    cookie_token = request.cookies.get(settings.csrf_cookie_name, "")

    if not header_token or not cookie_token:
        raise PermissionDeniedError(
            "Отсутствует CSRF-токен. Обновите страницу и повторите.", code="csrf_missing"
        )
    if not constant_time_equals(header_token, cookie_token):
        log.warning("csrf_mismatch", path=request.url.path)
        raise PermissionDeniedError("CSRF-токен не совпадает", code="csrf_mismatch")
    if not verify_csrf_token(header_token, hash_token(session_raw)):
        log.warning("csrf_bad_signature", path=request.url.path)
        raise PermissionDeniedError("CSRF-токен недействителен", code="csrf_invalid")


CsrfProtected = Depends(csrf_protect)


# Контекст организации (изоляция арендаторов)


class OrgContext:
    """Проверенный доступ пользователя к организации."""

    __slots__ = ("org", "member", "role", "user")

    def __init__(self, org: Organization, member: OrgMember, user: User) -> None:
        self.org = org
        self.member = member
        self.user = user
        self.role = OrgRole(member.role)

    def at_least(self, minimum: OrgRole) -> bool:
        return self.role.level >= minimum.level


def require_org_role(minimum: OrgRole) -> Callable[..., object]:
    """Зависимость: пользователь состоит в организации и его роль не ниже указанной."""

    async def dependency(slug: str, user: VerifiedUser, orgs: OrgServiceDep) -> OrgContext:
        org = await orgs.get_by_slug(slug)
        member = await orgs.get_membership(org.id, user.id)

        if member is None:
            # 404, а не 403: существование чужой организации - тоже информация.
            raise NotFoundError("Организация не найдена")

        context = OrgContext(org, member, user)
        if not context.at_least(minimum):
            raise PermissionDeniedError(
                f"Требуется роль не ниже «{minimum.value}», у вас «{context.role.value}»"
            )
        return context

    return dependency


OrgMemberContext = Annotated[OrgContext, Depends(require_org_role(OrgRole.MEMBER))]
OrgAdminContext = Annotated[OrgContext, Depends(require_org_role(OrgRole.ADMIN))]
OrgOwnerContext = Annotated[OrgContext, Depends(require_org_role(OrgRole.OWNER))]


async def get_public_org(slug: str, db: DbDep) -> Organization:
    """Организация для публичного контура: только если она сама себя публикует."""
    from sqlalchemy import select

    org = await db.scalar(
        select(Organization).where(Organization.slug == slug, Organization.is_public.is_(True))
    )
    if org is None:
        raise NotFoundError("Страница не найдена")
    return org


PublicOrg = Annotated[Organization, Depends(get_public_org)]


def parse_uuid(value: str, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise NotFoundError(f"Некорректный {field}") from None
