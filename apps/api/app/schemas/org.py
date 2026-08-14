"""Схемы организаций (арендаторов)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import Field, field_validator

from app.db.models import OrgRole
from app.schemas.common import Email, InputSchema, Schema, Trimmed

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

# Слаги, которые нельзя занимать: они либо уже заняты маршрутами фронтенда,
# либо выглядят как официальные страницы и годятся для фишинга.
RESERVED_SLUGS = frozenset(
    {
        "api",
        "admin",
        "app",
        "auth",
        "login",
        "logout",
        "register",
        "signup",
        "dashboard",
        "settings",
        "billing",
        "support",
        "help",
        "docs",
        "static",
        "assets",
        "public",
        "www",
        "mail",
        "root",
        "system",
        "security",
        "about",
        "new",
        "org",
        "orgs",
        "user",
        "users",
        "me",
        "health",
        "metrics",
    }
)


class OrgCreate(InputSchema):
    name: Trimmed = Field(min_length=2, max_length=120)
    slug: Trimmed = Field(min_length=3, max_length=64)
    description: Trimmed = Field(default="", max_length=2000)

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, v: str) -> str:
        v = v.lower()
        if not SLUG_RE.match(v):
            raise ValueError(
                "слаг: строчные латинские буквы, цифры и дефис; "
                "начинается и заканчивается буквой или цифрой"
            )
        if v in RESERVED_SLUGS:
            raise ValueError("этот слаг зарезервирован, выберите другой")
        return v


class OrgUpdate(InputSchema):
    name: Trimmed | None = Field(default=None, min_length=2, max_length=120)
    description: Trimmed | None = Field(default=None, max_length=2000)
    is_public: bool | None = None
    auto_approve_reviews: bool | None = None


class OrgOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    plan: str
    is_public: bool
    auto_approve_reviews: bool
    created_at: datetime


class OrgSummary(Schema):
    """Организация в списке «мои организации» - с ролью текущего пользователя."""

    id: uuid.UUID
    slug: str
    name: str
    plan: str
    role: str
    members_count: int
    pending_reviews: int


class PublicOrgOut(Schema):
    """То, что видит анонимный посетитель лендинга. Ни id, ни плана, ни настроек."""

    slug: str
    name: str
    description: str
    reviews_count: int
    average_rating: float


class MemberOut(Schema):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    joined_at: datetime


class MemberRoleUpdate(InputSchema):
    role: OrgRole


class InvitationCreate(InputSchema):
    email: Email
    role: OrgRole = OrgRole.MEMBER


class InvitationOut(Schema):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime


class InvitationAccept(InputSchema):
    token: str = Field(min_length=16, max_length=256)
