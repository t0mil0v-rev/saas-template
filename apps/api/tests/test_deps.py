"""Регрессии зависимостей сессии и CSRF."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.api.deps import csrf_protect, get_current_user_optional
from app.core.config import get_settings
from app.core.errors import PermissionDeniedError
from app.core.security import hash_token, issue_csrf_token
from starlette.requests import Request


def _request(*, cookie: str, origin: str = "", referer: str = "") -> Request:
    headers = [(b"cookie", cookie.encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    if referer:
        headers.append((b"referer", referer.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/logout",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "scheme": "http",
        }
    )


@pytest.mark.asyncio
async def test_csrf_requires_a_trusted_request_source() -> None:
    settings = get_settings()
    raw_session = "session-token"
    csrf = issue_csrf_token(hash_token(raw_session))
    cookie = f"{settings.cookie_name}={raw_session}; {settings.csrf_cookie_name}={csrf}"

    with pytest.raises(PermissionDeniedError, match="источник"):
        await csrf_protect(_request(cookie=cookie), settings)


@pytest.mark.asyncio
async def test_csrf_accepts_same_origin_referer_fallback() -> None:
    settings = get_settings()
    raw_session = "session-token"
    csrf = issue_csrf_token(hash_token(raw_session))
    cookie = f"{settings.cookie_name}={raw_session}; {settings.csrf_cookie_name}={csrf}"
    request = _request(
        cookie=cookie,
        referer=f"{settings.public_url}/app/security",
    )
    request.scope["headers"].append((b"x-csrf-token", csrf.encode()))

    await csrf_protect(request, settings)


@pytest.mark.asyncio
async def test_session_maintenance_is_committed_on_read_only_route() -> None:
    settings = get_settings()
    db = SimpleNamespace(
        sync_session=SimpleNamespace(new=set(), dirty={object()}, deleted=set()),
        commit=AsyncMock(),
    )
    auth = SimpleNamespace(db=db, resolve_session=AsyncMock(return_value=None))
    request = _request(cookie=f"{settings.cookie_name}=session-token")

    assert await get_current_user_optional(request, auth, settings) is None
    db.commit.assert_awaited_once()
