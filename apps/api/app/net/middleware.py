"""Конвейер middleware.

Всё написано как чистые ASGI-middleware, а не через
``BaseHTTPMiddleware``. Причина практическая: BaseHTTPMiddleware разворачивает
каждый ответ в отдельную задачу, из-за чего ломаются потоковые ответы,
теряются background-задачи и искажаются исключения. Чистый ASGI обходится без
этого и стоит дешевле.

Порядок важен. Ниже - как запрос проходит слои снаружи внутрь::

    ┌ RequestContext   присваивает request_id, вычисляет реальный IP, пишет access-log
    │ ┌ SecurityHeaders  навешивает заголовки на любой ответ, включая ошибки
    │ │ ┌ BodyLimit       режет слишком большие тела ДО чтения в память
    │ │ │ ┌ Timeout        не даёт запросу висеть вечно
    │ │ │ │ ┌ RateLimit     общий лимит на IP
    │ │ │ │ │ ┌ CORS
    │ │ │ │ │ │ └── приложение
"""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from collections.abc import Callable
from typing import ClassVar

from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.logging import client_ip_var, get_logger, request_id_var
from app.net.proxy import resolve_client
from app.net.ratelimit import RateLimiter

log = get_logger("http")

# Пути, которые не нужно засорять access-логом: healthcheck дёргается
# докером каждые 15 секунд и утопит всё остальное.
_QUIET_PATHS = frozenset({"/api/health/live", "/api/health/ready", "/metrics"})


def _json_response(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message},
            "request_id": request_id_var.get(),
        },
        headers=headers or {},
    )


# 1. Контекст запроса + access-log


class RequestContextMiddleware:
    """Присваивает request_id, определяет клиента, пишет структурированный лог.

    request_id берётся из заголовка ``X-Request-ID``, если он пришёл от
    доверенного прокси (тогда трассировка сквозная), иначе генерируется.
    Клиентскому заголовку доверять нельзя: им можно засорить или подделать логи.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        conn = HTTPConnection(scope)
        client = resolve_client(conn, self.settings)

        incoming = conn.headers.get("x-request-id", "")
        if client.via_trusted_proxy and incoming and len(incoming) <= 64 and incoming.isprintable():
            request_id = incoming
        else:
            request_id = uuid.uuid4().hex

        scope.setdefault("state", {})
        scope["state"]["client"] = client
        scope["state"]["request_id"] = request_id
        scope["state"]["csp_nonce"] = secrets.token_urlsafe(16)

        rid_token = request_id_var.set(request_id)
        ip_token = client_ip_var.set(client.ip)

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["x-request-id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            log.exception(
                "request_failed",
                method=scope.get("method"),
                path=scope.get("path"),
                duration_ms=round(duration_ms, 2),
            )
            raise
        else:
            path = scope.get("path", "")
            if path not in _QUIET_PATHS:
                duration_ms = (time.perf_counter() - started) * 1000
                # Медленные запросы - отдельным уровнем, чтобы их было видно
                # в логе без выборки по duration.
                level = log.warning if duration_ms > 2000 else log.info
                level(
                    "request",
                    method=scope.get("method"),
                    path=path,
                    status=status_code,
                    duration_ms=round(duration_ms, 2),
                    user_agent=conn.headers.get("user-agent", "")[:160],
                )
        finally:
            request_id_var.reset(rid_token)
            client_ip_var.reset(ip_token)


# 2. Заголовки безопасности


class SecurityHeadersMiddleware:
    """Заголовки безопасности на КАЖДЫЙ ответ, включая 4xx/5xx.

    Про CSP: это JSON-API, здесь ``default-src 'none'`` - самая строгая
    политика из возможных, и она корректна: браузер не должен подгружать
    из ответов API вообще ничего. CSP для HTML-страницы приложения задаёт
    Caddy (см. deploy/caddy/common.caddy) - там же, где отдаётся сама страница.

    Исключение - Swagger UI на /docs: ему нужны свои скрипты и стили.
    Он включается только при ENABLE_API_DOCS=true, то есть не в production.
    """

    _BASE: ClassVar[dict[str, str]] = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "no-referrer",
        "cross-origin-opener-policy": "same-origin",
        "cross-origin-resource-policy": "same-origin",
        "permissions-policy": (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=(), interest-cohort=()"
        ),
        # Ответы API не кэшируются нигде и никогда: в них персональные данные.
        "cache-control": "no-store",
    }
    _API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self._hsts = (
            "max-age=63072000; includeSubDomains; preload"
            if settings.secure_cookies and settings.tls_mode != "none"
            else None
        )

    def _docs_csp(self, nonce: str) -> str:
        # Swagger UI грузит скрипт и стили из того же origin (мы отдаём их
        # локально, без CDN - сервер корпорации N может не иметь выхода наружу).
        return (
            "default-src 'none'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        is_docs = path.startswith(("/docs", "/redoc", "/openapi.json"))
        nonce: str = scope.get("state", {}).get("csp_nonce", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in self._BASE.items():
                    if is_docs and key in ("x-frame-options", "cache-control"):
                        continue
                    headers.setdefault(key, value)
                headers.setdefault(
                    "content-security-policy",
                    self._docs_csp(nonce) if is_docs else self._API_CSP,
                )
                if self._hsts:
                    headers.setdefault("strict-transport-security", self._hsts)
                # Server-заголовок раскрывает версию - убираем.
                if "server" in headers:
                    del headers["server"]
            await send(message)

        await self.app(scope, receive, send_wrapper)


# 3. Ограничение размера тела запроса


class BodyLimitMiddleware:
    """Не даёт клиенту исчерпать память сервера большим телом запроса.

    Проверка двухступенчатая, и обе ступени обязательны:

    * ``Content-Length`` - отсекаем сразу, не прочитав ни байта;
    * подсчёт фактически прочитанного - на случай ``Transfer-Encoding: chunked``,
      где Content-Length отсутствует и заявленный размер проверить нечем.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = HTTPConnection(scope).headers
        declared = headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await self._reject(scope, receive, send, code="bad_content_length")
                return

        received = 0
        limit = self.max_bytes
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # Обрываем поток: приложение получит усечённое тело и
                    # ошибку разбора, а мы не выделим лишнюю память.
                    raise _BodyTooLarge
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, send_wrapper)
        except _BodyTooLarge:
            # Если ответ уже пошёл клиенту, второй http.response.start отправить
            # нельзя - ASGI-сервер на этом падает. Просто закрываем соединение.
            if not response_started:
                await self._reject(scope, receive, send)
            else:
                log.warning("body_limit_exceeded_after_response_started", path=scope.get("path"))

    async def _reject(
        self, scope: Scope, receive: Receive, send: Send, code: str = "payload_too_large"
    ) -> None:
        response = _json_response(413, code, f"Тело запроса превышает {self.max_bytes} байт")
        await response(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


# 4. Таймаут обработки


class TimeoutMiddleware:
    """Ограничивает время обработки одного запроса.

    Без этого один зависший запрос к внешнему сервису удерживает воркер
    навсегда: соединения копятся, пул БД выбирается, сервис встаёт целиком.
    """

    def __init__(self, app: ASGIApp, timeout_s: int) -> None:
        self.app = app
        self.timeout_s = timeout_s

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # WebSocket и SSE живут долго по своей природе - их не трогаем.
        if scope["type"] != "http" or self.timeout_s <= 0:
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await asyncio.wait_for(self.app(scope, receive, send_wrapper), timeout=self.timeout_s)
        except TimeoutError:
            log.error(
                "request_timeout",
                path=scope.get("path"),
                method=scope.get("method"),
                timeout_s=self.timeout_s,
            )
            if not response_started:
                response = _json_response(
                    503,
                    "request_timeout",
                    "Превышено время обработки запроса",
                    {"retry-after": "5"},
                )
                await response(scope, receive, send)
            # Если ответ уже начался, корректного выхода нет: соединение
            # закроется на середине тела. Клиент увидит обрыв - это честнее,
            # чем висеть.


# 5. Общий лимит частоты на IP


class RateLimitMiddleware:
    """Грубый общий лимит на IP. Точечные лимиты (вход, регистрация,
    отправка отзыва) висят на конкретных ручках - см. app.api.deps."""

    def __init__(
        self,
        app: ASGIApp,
        limiter: RateLimiter,
        *,
        limit_per_min: int,
        exempt_prefixes: tuple[str, ...] = ("/api/health", "/metrics"),
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.limit_per_min = limit_per_min
        self.exempt_prefixes = exempt_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path.startswith(self.exempt_prefixes):
            await self.app(scope, receive, send)
            return

        client = scope.get("state", {}).get("client")
        ip = client.ip if client else "unknown"

        result = await self.limiter.consume(
            f"global:{ip}",
            limit=self.limit_per_min,
            window_s=60,
            # Разрешаем короткий всплеск: загрузка страницы приложения - это
            # десяток параллельных запросов, и это нормальное поведение.
            burst=self.limit_per_min + 60,
        )
        if not result.allowed:
            log.warning("rate_limited", scope_key="global", path=path)
            response = _json_response(
                429,
                "rate_limited",
                "Слишком много запросов. Повторите позже.",
                {"retry-after": str(result.retry_after)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# Сборка конвейера

MiddlewareFactory = Callable[[ASGIApp], ASGIApp]


def build_stack(
    app: ASGIApp,
    settings: Settings,
    limiter: RateLimiter,
) -> ASGIApp:
    """Оборачивает приложение слоями. Последний обёрнутый - внешний."""
    wrapped: ASGIApp = app
    wrapped = RateLimitMiddleware(
        wrapped, limiter, limit_per_min=settings.rate_limit_global_per_min
    )
    wrapped = TimeoutMiddleware(wrapped, settings.request_timeout_s)
    wrapped = BodyLimitMiddleware(wrapped, settings.max_request_body_bytes)
    wrapped = SecurityHeadersMiddleware(wrapped, settings)
    wrapped = RequestContextMiddleware(wrapped, settings)
    return wrapped


__all__ = [
    "BodyLimitMiddleware",
    "RateLimitMiddleware",
    "RequestContextMiddleware",
    "SecurityHeadersMiddleware",
    "TimeoutMiddleware",
    "build_stack",
]
