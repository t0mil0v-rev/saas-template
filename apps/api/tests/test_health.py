"""Публичность health-ответов и поведение при отказе зависимостей."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app import __version__
from app.api.v1 import health
from app.core.config import Settings
from app.net.proxy import ClientInfo
from fastapi import Response, status
from starlette.requests import Request


def _client(ip: str) -> ClientInfo:
    return ClientInfo(
        ip=ip,
        peer_ip=ip,
        via_trusted_proxy=False,
        scheme="http",
        forwarded_chain=(),
    )


def _request(*, redis: object | None = None, degraded: bool = False) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            redis=redis,
            limiter=SimpleNamespace(degraded=degraded),
        )
    )
    return Request({"type": "http", "app": app, "headers": []})


def _settings(**changes: object) -> Settings:
    return Settings(app_env="development", metrics_allowed_nets="127.0.0.1/32", **changes)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_ready_hides_dependency_details_from_external_client(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(health.db_session, "ping", AsyncMock(return_value=True))
    monkeypatch.setattr(health.cache, "ping", AsyncMock(return_value=False))
    response = Response()

    result = await health.ready(_request(), response, _client("203.0.113.10"), _settings())

    assert response.status_code == status.HTTP_200_OK
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_reports_internal_dependency_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    redis = object()
    monkeypatch.setattr(health.db_session, "ping", AsyncMock(return_value=False))
    monkeypatch.setattr(health.cache, "ping", AsyncMock(return_value=False))
    response = Response()

    result = await health.ready(
        _request(redis=redis, degraded=True),
        response,
        _client("127.0.0.1"),
        _settings(),
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert result == {
        "status": "degraded",
        "checks": {"database": "fail", "redis": "fail"},
        "rate_limit_mode": "per-worker",
    }


@pytest.mark.asyncio
async def test_version_is_visible_only_inside_metrics_network() -> None:
    settings = _settings(app_name="Test API")

    assert await health.version(_client("203.0.113.10"), settings) == {"version": "hidden"}
    assert await health.version(_client("127.0.0.1"), settings) == {
        "version": __version__,
        "app": "Test API",
        "environment": "development",
    }


@pytest.mark.asyncio
async def test_metrics_can_be_disabled() -> None:
    response = await health.metrics(_client("127.0.0.1"), _settings(metrics_enabled=False))

    assert response.status_code == status.HTTP_404_NOT_FOUND
