"""Схемы аутентификации."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_validator

from app.core.security import MAX_PASSWORD_LENGTH
from app.schemas.common import Email, InputSchema, Schema, Trimmed


class RegisterRequest(InputSchema):
    email: Email
    password: str = Field(min_length=8, max_length=MAX_PASSWORD_LENGTH)
    full_name: Trimmed = Field(default="", max_length=120)
    # Имя организации: если задано, при регистрации создаётся собственная
    # организация и пользователь становится её владельцем.
    org_name: Trimmed = Field(default="", max_length=120)


class LoginRequest(InputSchema):
    email: Email
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    totp_code: str = Field(default="", max_length=16)


class UserOut(Schema):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    totp_enabled: bool
    email_verified_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime


class LoginResponse(Schema):
    user: UserOut
    # CSRF-токен дублируется в теле ответа, чтобы SPA могла положить его в
    # заголовок сразу, не читая cookie (и работать, если cookie помечена
    # SameSite=Strict на другом поддомене).
    csrf_token: str


class SessionOut(Schema):
    id: uuid.UUID
    ip_hash: str
    user_agent: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool = False


class ChangePasswordRequest(InputSchema):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=8, max_length=MAX_PASSWORD_LENGTH)


class ForgotPasswordRequest(InputSchema):
    email: Email


class ResetPasswordRequest(InputSchema):
    token: str = Field(min_length=16, max_length=256)
    new_password: str = Field(min_length=8, max_length=MAX_PASSWORD_LENGTH)


class VerifyEmailRequest(InputSchema):
    token: str = Field(min_length=16, max_length=256)


class UpdateProfileRequest(InputSchema):
    full_name: Trimmed = Field(default="", max_length=120)


# двухфакторная аутентификация


class TotpSetupResponse(Schema):
    secret: str
    """Base32-секрет. Показывается ОДИН раз, в БД лежит уже зашифрованным."""
    provisioning_uri: str
    """otpauth://-ссылка. QR-код рисует клиент - наружу мы не ходим."""


class TotpEnableRequest(InputSchema):
    code: str = Field(min_length=6, max_length=6)

    @field_validator("code")
    @classmethod
    def _digits_only(cls, v: str) -> str:
        v = v.strip().replace(" ", "")
        if not v.isdigit():
            raise ValueError("код должен состоять из шести цифр")
        return v


class TotpDisableRequest(InputSchema):
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class RecoveryCodesResponse(Schema):
    codes: list[str]
    """Показываются один раз. В БД хранятся только хеши."""


class RecoveryRegenerateRequest(InputSchema):
    """Перевыпуск резервных кодов требует повторной аутентификации:
    угнанная сессия без пароля и второго фактора не должна их сбросить."""

    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    totp_code: str = Field(default="", max_length=16)
