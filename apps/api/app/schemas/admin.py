"""Схемы административного контура (только для суперпользователя)."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.schemas.common import Schema


class AdminUserOut(Schema):
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    totp_enabled: bool
    email_verified_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    orgs_count: int


class AdminOrgOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    plan: str
    is_public: bool
    members_count: int
    reviews_count: int
    created_at: datetime


class AuditEntryOut(Schema):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    org_id: uuid.UUID | None
    action: str
    target_type: str
    target_id: str
    ip_hash: str
    meta: dict[str, object]
    created_at: datetime


class SystemStats(Schema):
    users_total: int
    users_active: int
    orgs_total: int
    reviews_total: int
    reviews_pending: int
