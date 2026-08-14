"""Клиент Redis.

Redis здесь необязателен. Он ускоряет и объединяет счётчики лимитов
между воркерами, но приложение полностью работоспособно без него. Это
осознанное решение: кэш не должен быть единой точкой отказа для входа
в систему.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger("cache")


async def create_redis(settings: Settings) -> Any | None:
    """Подключается к Redis. Возвращает None, если он не задан или недоступен."""
    if not settings.redis_url:
        log.info("redis_disabled", reason="REDIS_URL не задан, лимиты считаются в памяти")
        return None

    try:
        from redis.asyncio import Redis
    except ImportError:
        log.warning("redis_library_missing")
        return None

    try:
        client: Any = Redis.from_url(
            settings.redis_url,
            decode_responses=False,
            socket_connect_timeout=3,
            socket_timeout=3,
            # Redis лежит рядом в приватной сети; долгие ретраи только
            # удерживают воркер. Лучше быстро упасть в память.
            retry_on_timeout=False,
            health_check_interval=30,
            max_connections=32,
        )
        await client.ping()
    except Exception as exc:
        log.warning(
            "redis_unavailable",
            error=str(exc),
            action="работаю без Redis, лимиты считаются в памяти воркера",
        )
        return None

    log.info("redis_connected")
    return client


async def close_redis(client: Any | None) -> None:
    if client is None:
        return
    try:
        await client.aclose()
    except Exception:  # noqa: S110 - закрытие на выходе не критично
        pass


async def ping(client: Any | None) -> bool:
    if client is None:
        return False
    try:
        await client.ping()
        return True
    except Exception:
        return False
