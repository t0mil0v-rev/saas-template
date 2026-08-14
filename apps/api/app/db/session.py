"""Подключение к PostgreSQL: движок, пул, сессии.

Настройки пула подобраны так, чтобы приложение не могло исчерпать
``max_connections`` базы. Итоговое число соединений::

    (DB_POOL_SIZE + DB_MAX_OVERFLOW) × API_WORKERS  ≤  max_connections − резерв

Резерв нужен для psql администратора, миграций и бэкапа. Проверка этого
неравенства выполняется на старте - см. :func:`check_pool_budget`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger("db")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings) -> AsyncEngine:
    """Создаёт движок с безопасными настройками пула."""
    return create_async_engine(
        settings.database_url,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        # Проверка соединения перед выдачей из пула. Стоит один лёгкий
        # round-trip, зато переживает перезапуск БД и разрыв соединения
        # файрволом без единой ошибки у пользователя.
        pool_pre_ping=True,
        # Пересоздаём соединения раз в 30 минут: страховка от накопления
        # состояния на стороне сервера и от «протухших» NAT-трансляций.
        pool_recycle=1800,
        echo=False,
        connect_args={
            "server_settings": {
                # Ни один запрос не может висеть дольше заданного времени.
                # Без этого один тяжёлый SELECT удерживает соединение и
                # постепенно выбирает весь пул.
                "statement_timeout": str(settings.db_statement_timeout_ms),
                # Транзакция, забытая открытой, отпускает блокировки сама.
                "idle_in_transaction_session_timeout": "30000",
                "application_name": "saas-api",
                "timezone": "UTC",
            },
            "ssl": _ssl_arg(settings.db_sslmode),
            # Кэш подготовленных выражений отключаем: он несовместим с
            # пулерами уровня транзакции (PgBouncer в transaction mode),
            # а его выигрыш здесь пренебрежимо мал.
            "statement_cache_size": 0,
        },
    )


def _ssl_arg(sslmode: str) -> bool | str | None:
    """Преобразует libpq-подобный sslmode в аргумент asyncpg."""
    mapping: dict[str, bool | str | None] = {
        "disable": False,
        "prefer": None,  # asyncpg сам попробует TLS и откатится на открытое
        "require": True,
        "verify-ca": "verify-ca",
        "verify-full": "verify-full",
    }
    return mapping.get(sslmode, None)


def init_engine(settings: Settings | None = None) -> AsyncEngine:
    """Инициализирует глобальный движок (идемпотентно)."""
    global _engine, _sessionmaker
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_engine(settings)
        _sessionmaker = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,  # объекты остаются пригодны после commit
            autoflush=False,  # flush только явный: меньше сюрпризов в порядке SQL
        )
    return _engine


async def dispose_engine() -> None:
    """Закрывает все соединения. Вызывается при остановке воркера."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Транзакция как контекстный менеджер: commit при успехе, rollback при ошибке.

    Используется вне HTTP-запросов (CLI, фоновые задачи). Внутри запросов
    работает зависимость ``app.api.deps.get_db`` с той же семантикой.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def wait_for_db(settings: Settings, *, attempts: int = 30, delay_s: float = 2.0) -> None:
    """Ждёт готовности БД.

    Нужно при старте всего стека одновременно: контейнер API поднимается
    быстрее, чем PostgreSQL проходит recovery. Падать в этот момент -
    значит попасть в цикл рестартов вместо ожидания нескольких секунд.
    """
    engine = init_engine(settings)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            if attempt > 1:
                log.info("db_ready", attempts=attempt)
            return
        except Exception as exc:
            last_error = exc
            log.warning(
                "db_unavailable",
                attempt=attempt,
                of=attempts,
                retry_in_s=delay_s,
                error=type(exc).__name__,
            )
            await asyncio.sleep(delay_s)
    raise RuntimeError(f"База данных недоступна после {attempts} попыток: {last_error}")


async def check_pool_budget(settings: Settings) -> None:
    """Предупреждает, если пул может исчерпать max_connections базы."""
    engine = init_engine(settings)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SHOW max_connections"))
            max_conn = int(result.scalar_one())
    except Exception as exc:
        log.debug("pool_budget_check_skipped", error=str(exc))
        return

    demand = (settings.db_pool_size + settings.db_max_overflow) * settings.worker_count
    reserve = 10
    if demand + reserve > max_conn:
        log.warning(
            "db_pool_budget_exceeded",
            demand=demand,
            max_connections=max_conn,
            workers=settings.worker_count,
            hint=(
                "уменьшите DB_POOL_SIZE или API_WORKERS, либо поднимите "
                "max_connections в postgres - иначе часть запросов упрётся "
                "в 'too many clients already'"
            ),
        )


async def ping() -> bool:
    """Быстрая проверка живости для /api/health/ready."""
    try:
        engine = init_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
