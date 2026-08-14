"""Точка сборки ASGI-приложения.

Здесь соединяются все слои: конфигурация, БД, Redis, лимитер, собственный
конвейер middleware и роутеры. Экспортируемый объект ``app`` - это уже
обёрнутое middleware-стеком ASGI-приложение, его и запускает uvicorn.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from starlette.types import ASGIApp

from app import __version__
from app.api.router import api_router
from app.api.v1.health import metrics_router
from app.core import cache
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db import session as db
from app.net.middleware import build_stack
from app.net.ratelimit import RateLimiter
from app.services.mailer import Mailer

log = get_logger("main")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Инициализация и корректное освобождение ресурсов воркера.

    Всё, что открывается здесь (пул БД, соединение с Redis), закрывается в
    обратном порядке при остановке. Каждый воркер prefork-модели проходит
    этот цикл независимо.
    """
    settings: Settings = app.state.settings

    db.init_engine(settings)
    await db.wait_for_db(settings)
    await db.check_pool_budget(settings)

    redis_client = await cache.create_redis(settings)
    app.state.redis = redis_client
    # Лимитер уже создан при сборке и передан в middleware глобального лимита.
    # Не заменяем объект (иначе middleware останется со старым), а доливаем Redis.
    app.state.limiter.attach_redis(redis_client)
    app.state.mailer = Mailer(settings)

    log.info(
        "app_ready",
        version=__version__,
        env=settings.app_env,
        rate_limit="distributed" if redis_client else "per-worker",
        docs=settings.enable_api_docs,
    )
    try:
        yield
    finally:
        await cache.close_redis(redis_client)
        await db.dispose_engine()
        log.info("app_shutdown_complete")


def create_app(settings: Settings | None = None) -> ASGIApp:
    """Собирает приложение и оборачивает его собственным middleware-стеком."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    fastapi_app = FastAPI(
        title=settings.app_name,
        version=__version__,
        # Документацию отдаём только при явном разрешении (в проде - выкл).
        docs_url="/docs" if settings.enable_api_docs else None,
        redoc_url="/redoc" if settings.enable_api_docs else None,
        openapi_url="/openapi.json" if settings.enable_api_docs else None,
        lifespan=lifespan,
    )
    fastapi_app.state.settings = settings

    register_exception_handlers(fastapi_app)
    fastapi_app.include_router(api_router)
    fastapi_app.include_router(metrics_router)

    # Лимитер-заглушка на время до готовности lifespan: обращения к
    # app.state.limiter из зависимостей не должны падать при старте.
    fastapi_app.state.limiter = RateLimiter(None, enabled=settings.rate_limit_enabled)
    fastapi_app.state.mailer = Mailer(settings)
    fastapi_app.state.redis = None

    # Собственный конвейер middleware (см. app.net.middleware). CORS -
    # тоже наш слой, добавляется ближе всего к приложению.
    _install_cors(fastapi_app, settings)

    return build_stack(fastapi_app, settings, fastapi_app.state.limiter)


def _install_cors(app: FastAPI, settings: Settings) -> None:
    """CORS со строгим списком источников.

    Никаких ``allow_origins=["*"]`` вместе с ``allow_credentials=True`` -
    это недопустимая комбинация: она открыла бы cookie-сессии любому сайту.
    Разрешаем ровно известные Origin из настроек.
    """
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )


# Объект, который импортирует uvicorn (см. app.net.server.APP_IMPORT_STRING).
app: ASGIApp = create_app()
