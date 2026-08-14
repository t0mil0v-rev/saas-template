"""Базовый класс моделей и общие примитивы.

Соглашение об именовании ограничений задано явно. Без него Alembic
генерирует автоимена вида ``ck_users_1``, которые меняются при малейшей
правке модели, и миграции перестают быть воспроизводимыми.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    """UUID-первичный ключ.

    UUID вместо автоинкремента сознательно: последовательный id в URL
    раскрывает объём базы и позволяет перебирать чужие объекты
    (IDOR-разведка). Значение генерируется приложением - это упрощает
    вставку связанных записей одной транзакцией.
    """
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """created_at / updated_at, проставляемые сервером БД.

    Время берётся из ``now()`` PostgreSQL, а не из Python: часы контейнера
    приложения могут разъехаться, часы БД - единственный общий источник.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
