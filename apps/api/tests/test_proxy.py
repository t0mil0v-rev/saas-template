"""Тесты определения реального IP за прокси.

Это критично для безопасности: на IP завязаны лимиты, блокировка перебора и
антиспам. Ошибка здесь означает либо обход защиты, либо блокировку невиновных.
"""

from __future__ import annotations

from app.core.config import Settings
from app.net.proxy import resolve_client
from starlette.requests import HTTPConnection


def _conn(client_ip: str, headers: dict[str, str] | None = None) -> HTTPConnection:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "scheme": "http",
        "path": "/",
        "headers": raw_headers,
        "client": (client_ip, 12345),
        "server": ("api", 8000),
    }
    return HTTPConnection(scope)


def _settings(**over: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "development",
        "trusted_proxies": "10.0.0.0/8",
        "trusted_proxy_depth": 1,
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


class TestUntrustedPeer:
    def test_headers_ignored_from_untrusted(self) -> None:
        # Запрос напрямую (не от прокси): XFF игнорируется полностью.
        conn = _conn("203.0.113.5", {"x-forwarded-for": "1.2.3.4"})
        info = resolve_client(conn, _settings())
        assert info.ip == "203.0.113.5"
        assert info.via_trusted_proxy is False

    def test_spoofed_xff_cannot_change_ip(self) -> None:
        # Классическая атака: подставить чужой IP заголовком. Не должна работать.
        conn = _conn("203.0.113.5", {"x-forwarded-for": "127.0.0.1, 10.0.0.1"})
        info = resolve_client(conn, _settings())
        assert info.ip == "203.0.113.5"


class TestTrustedProxy:
    def test_single_proxy_extracts_client(self) -> None:
        conn = _conn("10.0.0.1", {"x-forwarded-for": "198.51.100.7"})
        info = resolve_client(conn, _settings(trusted_proxy_depth=1))
        assert info.ip == "198.51.100.7"
        assert info.via_trusted_proxy is True

    def test_depth_picks_correct_element(self) -> None:
        # depth=1 → берём последний элемент справа (его дописал наш прокси),
        # а НЕ первый, который может подделать клиент.
        conn = _conn("10.0.0.1", {"x-forwarded-for": "evil-spoof, 198.51.100.7"})
        info = resolve_client(conn, _settings(trusted_proxy_depth=1))
        assert info.ip == "198.51.100.7"

    def test_two_proxies(self) -> None:
        conn = _conn(
            "10.0.0.1",
            {"x-forwarded-for": "198.51.100.7, 10.0.0.9"},
        )
        info = resolve_client(conn, _settings(trusted_proxy_depth=2))
        assert info.ip == "198.51.100.7"

    def test_short_chain_fails_closed_to_peer(self) -> None:
        conn = _conn("10.0.0.1", {"x-forwarded-for": "198.51.100.7"})
        info = resolve_client(conn, _settings(trusted_proxy_depth=2))
        assert info.ip == "10.0.0.1"

    def test_forwarded_proto_applied(self) -> None:
        conn = _conn(
            "10.0.0.1",
            {"x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "https"},
        )
        info = resolve_client(conn, _settings())
        assert info.scheme == "https"

    def test_ipv6_with_port(self) -> None:
        conn = _conn("10.0.0.1", {"x-forwarded-for": "[2001:db8::1]:443"})
        info = resolve_client(conn, _settings())
        assert info.ip == "2001:db8::1"

    def test_empty_chain_falls_back_to_peer(self) -> None:
        conn = _conn("10.0.0.1", {"x-forwarded-for": ""})
        info = resolve_client(conn, _settings())
        assert info.ip == "10.0.0.1"


class TestNoTrustedProxies:
    def test_empty_config_uses_peer(self) -> None:
        conn = _conn("203.0.113.5", {"x-forwarded-for": "1.2.3.4"})
        info = resolve_client(conn, _settings(trusted_proxies=""))
        assert info.ip == "203.0.113.5"
        assert info.via_trusted_proxy is False
