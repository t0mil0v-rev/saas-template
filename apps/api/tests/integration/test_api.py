"""Critical browser flows through the complete ASGI stack."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from app.core.config import Settings
from app.db.models import MailOutbox, Organization, User
from app.db.session import get_sessionmaker
from app.main import create_app
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


@asynccontextmanager
async def api_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_registration_commits_user_org_and_mail_atomically(
    integration_settings: Settings,
) -> None:
    settings = integration_settings.model_copy(update={"require_email_verification": True})
    async with api_client(settings) as client:
        response = await client.post(
            "/api/auth/register",
            json={
                "email": "new@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "New User",
                "org_name": "New Organization",
            },
        )
        assert response.status_code == 202

        maker = get_sessionmaker()
        async with maker() as db:
            assert await db.scalar(select(func.count()).select_from(User)) == 1
            assert await db.scalar(select(func.count()).select_from(Organization)) == 1
            assert await db.scalar(select(func.count()).select_from(MailOutbox)) == 1


@pytest.mark.asyncio
async def test_session_and_csrf_flow(integration_settings: Settings) -> None:
    async with api_client(integration_settings) as client:
        register = await client.post(
            "/api/auth/register",
            json={
                "email": "session@example.com",
                "password": "correct-horse-battery-staple",
                "full_name": "Session User",
            },
        )
        assert register.status_code == 202
        login = await client.post(
            "/api/auth/login",
            json={"email": "session@example.com", "password": "correct-horse-battery-staple"},
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]

        assert (await client.get("/api/auth/me")).status_code == 200
        assert (
            await client.patch("/api/auth/me", json={"full_name": "Blocked"})
        ).status_code == 403
        updated = await client.patch(
            "/api/auth/me",
            json={"full_name": "Updated"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:5173"},
        )
        assert updated.status_code == 200
        assert updated.json()["full_name"] == "Updated"
