"""Проверки здоровья и метрики.

Две разные проверки - это не дублирование:

* ``/live``  - «процесс жив». Не трогает БД. По нему оркестратор решает,
  надо ли ПЕРЕЗАПУСТИТЬ контейнер. Если завязать live на базу, то падение
  БД вызовет лавину перезапусков всех воркеров - ровно тогда, когда
  система и так в беде.
* ``/ready`` - «готов принимать трафик». Проверяет зависимости. По нему
  балансировщик решает, слать ли сюда запросы.
"""

from __future__ import annotations

import ipaddress
import os
import time
from typing import Any

from fastapi import APIRouter, Request, Response, status

from app import __version__
from app.api.deps import ClientDep, SettingsDep
from app.core import cache
from app.db import session as db_session

router = APIRouter(tags=["health"])

_STARTED_AT = time.time()


@router.get("/health/live", summary="Жив ли процесс")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", summary="Готов ли принимать трафик")
async def ready(
    request: Request,
    response: Response,
    client: ClientDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    db_ok = await db_session.ping()
    redis_client = getattr(request.app.state, "redis", None)
    redis_ok = await cache.ping(redis_client)

    # Redis необязателен: без него сервис работает, просто лимиты считаются
    # на воркер. Поэтому готовность определяется ТОЛЬКО базой.
    ready_now = db_ok
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    result: dict[str, Any] = {
        "status": "ok" if ready_now else "degraded",
    }
    if _is_internal(client.ip, settings):
        result["checks"] = {
            "database": "ok" if db_ok else "fail",
            "redis": "ok" if redis_ok else ("disabled" if redis_client is None else "fail"),
        }
        result["rate_limit_mode"] = (
            "distributed" if not request.app.state.limiter.degraded else "per-worker"
        )
    return result


@router.get("/health/version", summary="Версия сборки")
async def version(client: ClientDep, settings: SettingsDep) -> dict[str, str]:
    if not _is_internal(client.ip, settings):
        return {"version": "hidden"}
    return {
        "version": __version__,
        "app": settings.app_name,
        "environment": settings.app_env,
    }


# Prometheus

metrics_router = APIRouter(tags=["health"])


def _is_internal(ip: str, settings: SettingsDep) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in settings.metrics_networks)


@metrics_router.get("/metrics", include_in_schema=False)
async def metrics(client: ClientDep, settings: SettingsDep) -> Response:
    """Метрики в формате Prometheus.

    Доступ ограничен списком сетей: выдача раскрывает внутреннее устройство
    сервиса (пути, коды ответов, объёмы) и наружу смотреть не должна.
    """
    if not settings.metrics_enabled:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    if not _is_internal(client.ip, settings):
        # 404, а не 403: наружу не подтверждаем даже существование эндпоинта.
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        # Prefork: собираем счётчики всех воркеров из общего каталога,
        # иначе каждый скрейп попадал бы в случайный воркер.
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        payload = generate_latest(registry)
    else:
        payload = generate_latest()

    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
