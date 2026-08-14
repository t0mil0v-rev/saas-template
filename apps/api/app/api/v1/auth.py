"""Роуты аутентификации.

Все обработчики, меняющие состояние, делают ``await db.commit()`` явно -
см. пояснение в :mod:`app.api.deps`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    AuthServiceDep,
    CsrfProtected,
    CurrentUser,
    DbDep,
    LimiterDep,
    OrgServiceDep,
    SettingsDep,
    auth_rate_limit,
    clear_session_cookies,
    set_session_cookies,
)
from app.core.errors import AuthenticationError, NotFoundError, RateLimitError
from app.core.security import hash_token, issue_csrf_token
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    RecoveryCodesResponse,
    RecoveryRegenerateRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SessionOut,
    TotpDisableRequest,
    TotpEnableRequest,
    TotpSetupResponse,
    UpdateProfileRequest,
    UserOut,
    VerifyEmailRequest,
)
from app.schemas.common import Message

router = APIRouter(prefix="/auth", tags=["auth"])

# Один и тот же текст для «зарегистрировали» и «адрес уже занят».
# Разные ответы дали бы возможность перечислять учётные записи.
_REGISTER_REPLY = (
    "Заявка принята. Если адрес свободен, на него отправлено письмо "
    "со ссылкой для подтверждения."
)
_REGISTER_REPLY_NO_VERIFY = (
    "Заявка принята. Если адрес свободен, учётная запись создана - " "можете войти."
)


def _slug_from_name(name: str) -> str:
    """Черновой слаг из названия организации. Пользователь сможет сменить его."""
    import re

    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    if len(base) < 3:
        base = f"org-{uuid.uuid4().hex[:8]}"
    return f"{base}-{uuid.uuid4().hex[:6]}"


# Регистрация и подтверждение адреса


@router.post(
    "/register",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(auth_rate_limit)],
    summary="Регистрация",
)
async def register(
    payload: RegisterRequest,
    auth: AuthServiceDep,
    orgs: OrgServiceDep,
    db: DbDep,
    settings: SettingsDep,
) -> Message:
    user = await auth.register(payload.email, payload.password, payload.full_name)

    if user is not None:
        if payload.org_name:
            await orgs.create(user, name=payload.org_name, slug=_slug_from_name(payload.org_name))
        if settings.require_email_verification:
            await auth.send_verification(user)
        await db.commit()

    return Message(
        detail=_REGISTER_REPLY if settings.require_email_verification else _REGISTER_REPLY_NO_VERIFY
    )


@router.post("/verify-email", summary="Подтвердить адрес по токену из письма")
async def verify_email(
    payload: VerifyEmailRequest,
    auth: AuthServiceDep,
    db: DbDep,
    _: None = Depends(auth_rate_limit),
) -> Message:
    await auth.verify_email(payload.token)
    await db.commit()
    return Message(detail="Адрес подтверждён. Теперь можно войти.")


@router.post(
    "/resend-verification",
    dependencies=[Depends(auth_rate_limit)],
    summary="Выслать письмо с подтверждением повторно",
)
async def resend_verification(
    payload: ForgotPasswordRequest,
    auth: AuthServiceDep,
    db: DbDep,
) -> Message:
    from sqlalchemy import select

    from app.db.models import User

    user = await auth.db.scalar(select(User).where(User.email == payload.email))
    if user is not None and not user.is_verified:
        await auth.send_verification(user)
        await db.commit()
    # Ответ одинаковый в любом случае.
    return Message(detail="Если адрес зарегистрирован и не подтверждён, письмо отправлено.")


# Вход и выход


@router.post("/login", dependencies=[Depends(auth_rate_limit)], summary="Вход")
async def login(
    payload: LoginRequest,
    response: Response,
    auth: AuthServiceDep,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> LoginResponse:
    # Второй лимит - на учётную запись, а не на IP. Он закрывает случай
    # распределённого перебора одного аккаунта с сотен адресов, где
    # лимит по IP бесполезен.
    account_bucket = f"login-account:{payload.email}"
    quota = await limiter.consume(
        account_bucket, limit=settings.login_max_failures * 2, window_s=600
    )
    if not quota.allowed:
        raise RateLimitError(
            "Слишком много попыток входа в эту учётную запись. Повторите позже.",
            headers={"Retry-After": str(quota.retry_after)},
        )

    try:
        user = await auth.login(payload.email, payload.password, payload.totp_code)
    except AuthenticationError:
        # Неудача фиксируется в аудите внутри сервиса - её нужно сохранить,
        # поэтому коммитим перед тем, как ошибка уйдёт наверх.
        await db.commit()
        raise

    session_token, session_obj = await auth.create_session(user)
    csrf = issue_csrf_token(session_obj.token_hash)
    await db.commit()

    # Успешный вход обнуляет счётчик попыток по этой учётной записи.
    await limiter.reset(account_bucket)

    set_session_cookies(response, settings, session_token, csrf)
    return LoginResponse(user=UserOut.model_validate(user), csrf_token=csrf)


@router.post("/logout", dependencies=[CsrfProtected], summary="Выход")
async def logout(
    request: Request,
    response: Response,
    user: CurrentUser,
    auth: AuthServiceDep,
    db: DbDep,
    settings: SettingsDep,
) -> Message:
    session_obj = getattr(request.state, "session", None)
    if session_obj is not None:
        await auth.revoke_session(session_obj, reason="logout")
        await db.commit()
    clear_session_cookies(response, settings)
    return Message(detail="Сеанс завершён")


@router.post(
    "/logout-all",
    dependencies=[CsrfProtected],
    summary="Завершить все сеансы, включая текущий",
)
async def logout_all(
    response: Response,
    user: CurrentUser,
    auth: AuthServiceDep,
    db: DbDep,
    settings: SettingsDep,
) -> Message:
    count = await auth.revoke_all_sessions(user)
    await db.commit()
    clear_session_cookies(response, settings)
    return Message(detail=f"Завершено сеансов: {count}")


@router.get("/me", summary="Текущий пользователь")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me", dependencies=[CsrfProtected], summary="Изменить профиль")
async def update_profile(payload: UpdateProfileRequest, user: CurrentUser, db: DbDep) -> UserOut:
    user.full_name = payload.full_name
    await db.commit()
    return UserOut.model_validate(user)


@router.get("/csrf", summary="Получить свежий CSRF-токен")
async def refresh_csrf(
    request: Request, response: Response, user: CurrentUser, settings: SettingsDep
) -> Message:
    """Перевыпуск CSRF-токена.

    Нужен, если вкладка провисела дольше времени жизни cookie CSRF или
    пользователь очистил её вручную: без токена ни одна форма не отправится.
    """
    raw = request.cookies.get(settings.cookie_name, "")
    csrf = issue_csrf_token(hash_token(raw))
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf,
        max_age=settings.session_absolute_ttl_h * 3600,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return Message(detail=csrf)


# Сеансы


@router.get("/sessions", summary="Мои активные сеансы")
async def list_sessions(
    request: Request, user: CurrentUser, auth: AuthServiceDep
) -> list[SessionOut]:
    current = getattr(request.state, "session", None)
    current_id = current.id if current else None
    return [
        SessionOut(
            id=s.id,
            ip_hash=s.ip_hash,
            user_agent=s.user_agent,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            expires_at=s.expires_at,
            current=(s.id == current_id),
        )
        for s in await auth.list_sessions(user)
    ]


@router.delete("/sessions/{session_id}", dependencies=[CsrfProtected], summary="Завершить сеанс")
async def revoke_session(
    session_id: uuid.UUID, user: CurrentUser, auth: AuthServiceDep, db: DbDep
) -> Message:
    for session_obj in await auth.list_sessions(user):
        if session_obj.id == session_id:
            await auth.revoke_session(session_obj, reason="revoked_by_user")
            await db.commit()
            return Message(detail="Сеанс завершён")
    raise NotFoundError("Сеанс не найден")


# Пароль


@router.post("/password/change", dependencies=[CsrfProtected], summary="Сменить пароль")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: CurrentUser,
    auth: AuthServiceDep,
    db: DbDep,
) -> Message:
    await auth.change_password(user, payload.current_password, payload.new_password)
    # Текущий сеанс оставляем - иначе пользователя выбрасывает из интерфейса
    # сразу после смены пароля. Все остальные завершаем.
    current = getattr(request.state, "session", None)
    await auth.revoke_all_sessions(user, except_id=current.id if current else None)
    await db.commit()
    return Message(detail="Пароль изменён, остальные сеансы завершены")


@router.post(
    "/password/forgot",
    dependencies=[Depends(auth_rate_limit)],
    summary="Запросить восстановление пароля",
)
async def forgot_password(
    payload: ForgotPasswordRequest, auth: AuthServiceDep, db: DbDep
) -> Message:
    await auth.request_password_reset(payload.email)
    await db.commit()
    return Message(
        detail="Если такой адрес зарегистрирован, на него отправлено письмо с инструкцией."
    )


@router.post(
    "/password/reset",
    dependencies=[Depends(auth_rate_limit)],
    summary="Задать новый пароль по токену",
)
async def reset_password(payload: ResetPasswordRequest, auth: AuthServiceDep, db: DbDep) -> Message:
    await auth.reset_password(payload.token, payload.new_password)
    await db.commit()
    return Message(detail="Пароль изменён. Войдите с новым паролем.")


# Двухфакторная аутентификация


@router.post("/2fa/setup", dependencies=[CsrfProtected], summary="Получить секрет TOTP")
async def totp_setup(user: CurrentUser, auth: AuthServiceDep, db: DbDep) -> TotpSetupResponse:
    secret, uri = await auth.totp_setup(user)
    await db.commit()
    return TotpSetupResponse(secret=secret, provisioning_uri=uri)


@router.post("/2fa/enable", dependencies=[CsrfProtected], summary="Включить 2FA")
async def totp_enable(
    payload: TotpEnableRequest, user: CurrentUser, auth: AuthServiceDep, db: DbDep
) -> RecoveryCodesResponse:
    codes = await auth.totp_enable(user, payload.code)
    await db.commit()
    return RecoveryCodesResponse(codes=codes)


@router.post("/2fa/disable", dependencies=[CsrfProtected], summary="Отключить 2FA")
async def totp_disable(
    payload: TotpDisableRequest, user: CurrentUser, auth: AuthServiceDep, db: DbDep
) -> Message:
    await auth.totp_disable(user, payload.password)
    await db.commit()
    return Message(detail="Двухфакторная аутентификация отключена")


@router.post(
    "/2fa/recovery-codes",
    dependencies=[CsrfProtected, Depends(auth_rate_limit)],
    summary="Перевыпустить резервные коды",
)
async def regenerate_recovery_codes(
    payload: RecoveryRegenerateRequest,
    user: CurrentUser,
    auth: AuthServiceDep,
    db: DbDep,
) -> RecoveryCodesResponse:
    # Повторная аутентификация: перевыпуск кодов недоступен по одной лишь
    # угнанной сессии - нужен пароль и, если включён, второй фактор.
    await auth.reauthenticate(user, payload.password, payload.totp_code)
    codes = await auth.regenerate_recovery_codes(user)
    await db.commit()
    return RecoveryCodesResponse(codes=codes)


@router.get("/2fa/status", summary="Состояние 2FA")
async def totp_status(user: CurrentUser, auth: AuthServiceDep) -> dict[str, object]:
    return {
        "enabled": user.totp_enabled,
        "recovery_codes_left": await auth.count_unused_recovery_codes(user),
    }
