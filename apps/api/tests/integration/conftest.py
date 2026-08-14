"""Fixtures for tests that require real infrastructure."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.core.config import Settings, get_settings
from app.db import session as db_session
from sqlalchemy import text


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("set RUN_INTEGRATION=1 to run live infrastructure tests")
    return Settings(
        app_env="development",
        require_email_verification=False,
        mail_transport="console",
        redis_url="",
        api_workers=1,
    )


@pytest_asyncio.fixture(autouse=True)
async def clean_database(integration_settings: Settings) -> AsyncIterator[None]:
    await db_session.dispose_engine()
    engine = db_session.init_engine(integration_settings)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE mail_outbox, audit_log, reviews, invitations, org_members, "
                "recovery_codes, email_tokens, sessions, organizations, users CASCADE"
            )
        )
    get_settings.cache_clear()
    yield
    await db_session.dispose_engine()
    get_settings.cache_clear()
