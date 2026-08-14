"""Общие типы схем.

Здесь же живёт нормализация e-mail. Она обязана быть в ОДНОМ месте: если
регистрация сохранит ``User@Example.COM``, а вход будет искать
``user@example.com``, пользователь просто не сможет войти - и это одна из
самых частых ошибок в самописной аутентификации.
"""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field

T = TypeVar("T")


def _normalize_email(value: object) -> object:
    if isinstance(value, str):
        return value.strip().lower()
    return value


Email = Annotated[EmailStr, BeforeValidator(_normalize_email)]
"""E-mail, приведённый к нижнему регистру и без пробелов по краям."""


def _strip(value: object) -> object:
    if isinstance(value, str):
        # Убираем и обычные пробелы, и невидимые символы, которыми часто
        # пытаются обойти проверки длины и уникальности.
        return value.strip().replace("​", "").replace("﻿", "")
    return value


Trimmed = Annotated[str, BeforeValidator(_strip)]


class Schema(BaseModel):
    """Базовая схема ответа: читает атрибуты ORM-объектов напрямую."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class InputSchema(BaseModel):
    """Базовая схема ввода.

    ``extra="forbid"`` - не вежливость, а защита: неизвестное поле в теле
    запроса означает либо опечатку клиента, либо попытку подсунуть
    ``is_superuser: true``. Молча игнорировать такое нельзя.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page(Schema, Generic[T]):
    """Страница результатов."""

    items: list[T]
    total: int = Field(description="Всего записей, удовлетворяющих фильтру")
    limit: int
    offset: int


class Message(Schema):
    """Ответ на действие без содержательного результата."""

    detail: str


class PaginationParams(InputSchema):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100_000)
