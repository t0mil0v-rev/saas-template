"""Доменные ошибки и обработчики исключений.

Правила, которым подчиняются ответы об ошибках:

1. Наружу уходит только то, что клиенту положено знать. Трассировки, SQL,
   имена таблиц и внутренние адреса не покидают сервер - они уходят в лог.
2. У каждой ошибки есть стабильный машиночитаемый ``code``: фронтенд
   ветвится по нему, а не по тексту сообщения.
3. Каждый ответ несёт ``request_id`` - по нему саппорт находит запись в логе.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var

log = get_logger("errors")


class AppError(Exception):
    """Базовая ожидаемая ошибка приложения."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"
    message: str = "Некорректный запрос"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}
        self.headers = headers or {}
        super().__init__(self.message)


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"
    message = "Данные не прошли проверку"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    message = "Требуется вход в систему"


class InvalidCredentialsError(AuthenticationError):
    code = "invalid_credentials"
    # Одинаковый текст для «нет такого пользователя» и «неверный пароль»:
    # иначе форма логина превращается в оракул для перебора адресов.
    message = "Неверный e-mail или пароль"


class TwoFactorRequiredError(AuthenticationError):
    code = "totp_required"
    message = "Требуется код двухфакторной аутентификации"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "Недостаточно прав"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Объект не найден"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "Конфликт состояния"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Слишком много запросов, попробуйте позже"


class AccountLockedError(AppError):
    status_code = status.HTTP_423_LOCKED
    code = "account_locked"
    message = "Учётная запись временно заблокирована из-за неудачных попыток входа"


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "payload_too_large"
    message = "Тело запроса слишком велико"


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "Сервис временно недоступен"


def _envelope(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message},
        "request_id": request_id_var.get(),
    }
    if details:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    """Вешает обработчики на приложение. Вызывается один раз при сборке."""

    @app.exception_handler(AppError)
    async def _app_error(_r: Request, exc: AppError) -> JSONResponse:
        # 5xx - наша вина, пишем как ошибку; 4xx - вина клиента, пишем как info.
        logger = log.error if exc.status_code >= 500 else log.info
        logger(
            "app_error",
            code=exc.code,
            status=exc.status_code,
            detail=exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, details=exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_r: Request, exc: RequestValidationError) -> JSONResponse:
        # Приводим ошибки pydantic к компактному виду {поле: сообщение}.
        fields: dict[str, str] = {}
        for err in exc.errors():
            location = ".".join(str(p) for p in err["loc"] if p not in ("body", "query", "path"))
            fields[location or "body"] = err["msg"]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_envelope(
                "validation_error",
                "Данные не прошли проверку",
                details={"fields": fields},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_r: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            413: "payload_too_large",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, str(exc.detail)),
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Единственное место, где может утечь внутренняя информация.
        # Наружу - обезличенный текст, внутрь - полный traceback.
        log.exception(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            exc_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(
                "internal_error",
                "Внутренняя ошибка сервера. Обратитесь в поддержку и укажите request_id.",
            ),
        )
