"""Схемы отзывов."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models import ReviewStatus
from app.schemas.common import Email, InputSchema, Schema, Trimmed


class ReviewCreate(InputSchema):
    """Публичная форма отзыва.

    Поле ``website`` - приманка (honeypot). Оно скрыто в вёрстке и человеком
    не заполняется; бот, который слепо заполняет все поля формы, выдаёт себя.
    Дёшево, не мешает пользователю и не требует внешних сервисов вроде
    reCAPTCHA - что важно, когда сервер стоит в закрытом контуре.
    """

    author_name: Trimmed = Field(min_length=2, max_length=80)
    author_email: Email | None = None
    rating: int = Field(ge=1, le=5)
    title: Trimmed = Field(default="", max_length=120)
    body: Trimmed = Field(min_length=10, max_length=5000)
    website: str = Field(default="", max_length=200, exclude=True)


class ReviewOut(Schema):
    """Публичное представление: без e-mail автора и без служебных полей."""

    id: uuid.UUID
    author_name: str
    rating: int
    title: str
    body: str
    created_at: datetime


class ReviewAdminOut(Schema):
    """Представление для модератора: со всеми служебными данными."""

    id: uuid.UUID
    author_name: str
    author_email: str
    rating: int
    title: str
    body: str
    status: str
    ip_hash: str
    user_agent: str
    moderated_by: uuid.UUID | None
    moderated_at: datetime | None
    moderation_note: str
    created_at: datetime


class ReviewModerate(InputSchema):
    status: ReviewStatus
    note: Trimmed = Field(default="", max_length=500)


class ReviewStats(Schema):
    total: int
    average: float
    distribution: dict[int, int]
    """Сколько отзывов на каждую оценку: {5: 12, 4: 3, ...}"""
