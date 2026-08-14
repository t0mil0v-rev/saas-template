"""Окружение Alembic.

Две особенности против стандартного шаблона:

1. Асинхронный драйвер. Миграции идут тем же asyncpg, что и приложение,
   поэтому в образ не нужно тащить второй драйвер (psycopg2) со своими
   системными зависимостями.
2. Консультативная блокировка PostgreSQL. Перед применением миграций
   берётся ``pg_advisory_xact_lock``. Если стек поднимается несколькими
   узлами одновременно, миграции выполнит ровно один - остальные подождут
   и увидят уже применённую схему. Без этого параллельный ``CREATE TABLE``
   даёт гонку и падение деплоя.
"""

from __future__ import annotations

import asyncio
from typing import Any

from alembic import context
from app.core.config import get_settings
from app.db.base import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Импорт ради регистрации моделей в Base.metadata - без него autogenerate
# решит, что все таблицы лишние, и сгенерирует миграцию на их удаление.
from app.db import models  # noqa: F401  isort:skip

config = context.config
target_metadata = Base.metadata

# Произвольное, но постоянное число: ключ консультативной блокировки.
MIGRATION_LOCK_ID = 0x5AA5_0001


def _url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Режим --sql: печатает SQL, не подключаясь к базе."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # Один файл на ревизию, включая изменения индексов.
        render_as_batch=False,
    )
    with context.begin_transaction():
        # Блокировка держится до конца транзакции и снимается автоматически,
        # в том числе если процесс убьют.
        connection.exec_driver_sql(f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK_ID})")
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(
        _url(),
        poolclass=pool.NullPool,  # одноразовое соединение, пул тут не нужен
        connect_args={"statement_cache_size": 0},
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


def main() -> Any:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        asyncio.run(run_migrations_online())


main()
