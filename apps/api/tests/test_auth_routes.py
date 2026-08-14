"""Границы транзакции HTTP-маршрутов аутентификации."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.api.v1.auth import login
from app.core.config import get_settings
from app.core.errors import InvalidCredentialsError
from app.net.ratelimit import RateLimitResult
from app.schemas.auth import LoginRequest
from fastapi import Response


def _login_args(error: Exception) -> tuple[object, object, object]:
    auth = SimpleNamespace(login=AsyncMock(side_effect=error))
    db = SimpleNamespace(commit=AsyncMock())
    limiter = SimpleNamespace(
        consume=AsyncMock(return_value=RateLimitResult(True, remaining=1, retry_after=0))
    )
    return auth, db, limiter


@pytest.mark.asyncio
async def test_expected_login_failure_commits_audit_event() -> None:
    auth, db, limiter = _login_args(InvalidCredentialsError())
    with pytest.raises(InvalidCredentialsError):
        await login(
            LoginRequest(email="user@example.com", password="wrong"),
            Response(),
            auth,
            db,
            get_settings(),
            limiter,
        )
    db.commit.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_unexpected_login_failure_does_not_commit_partial_state() -> None:
    auth, db, limiter = _login_args(RuntimeError("database failure"))
    with pytest.raises(RuntimeError):
        await login(
            LoginRequest(email="user@example.com", password="wrong"),
            Response(),
            auth,
            db,
            get_settings(),
            limiter,
        )
    db.commit.assert_not_awaited()  # type: ignore[attr-defined]
