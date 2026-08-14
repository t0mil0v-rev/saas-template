"""Проверки сокета и жизненного цикла собственного HTTP-супервизора."""

from __future__ import annotations

from typing import Any

import pytest
from app.core.config import Settings
from app.net import server


class _FakeSocket:
    def __init__(self) -> None:
        self.options: list[tuple[int, int, int]] = []
        self.bound: tuple[str, int] | None = None
        self.backlog = 0
        self.inheritable = False
        self.closed = False

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.options.append((level, option, value))

    def bind(self, address: tuple[str, int]) -> None:
        self.bound = address

    def listen(self, backlog: int) -> None:
        self.backlog = backlog

    def set_inheritable(self, inheritable: bool) -> None:
        self.inheritable = inheritable

    def close(self) -> None:
        self.closed = True


class _DeadProcess:
    pid = 123
    exitcode = 1

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        del timeout


def _settings(**changes: object) -> Settings:
    return Settings(app_env="development", metrics_enabled=False, **changes)  # type: ignore[arg-type]


def test_create_listening_socket_applies_reuseport(monkeypatch: Any) -> None:
    fake = _FakeSocket()
    monkeypatch.setattr(server.socket, "socket", lambda *_: fake)
    monkeypatch.setattr(server, "_SO_REUSEPORT", 99)

    result = server.create_listening_socket(
        _settings(api_host="127.0.0.1", api_port=9123, listen_backlog=321),
        reuse_port=True,
    )

    assert result is fake
    assert (server.socket.SOL_SOCKET, 99, 1) in fake.options
    assert fake.bound == ("127.0.0.1", 9123)
    assert fake.backlog == 321
    assert fake.inheritable


def test_create_listening_socket_closes_after_bind_error(monkeypatch: Any) -> None:
    fake = _FakeSocket()

    def fail_bind(_address: tuple[str, int]) -> None:
        raise OSError("port is busy")

    fake.bind = fail_bind  # type: ignore[method-assign]
    monkeypatch.setattr(server.socket, "socket", lambda *_: fake)

    with pytest.raises(OSError, match="busy"):
        server.create_listening_socket(_settings(), reuse_port=False)
    assert fake.closed


def test_uvicorn_config_uses_available_accelerators(monkeypatch: Any) -> None:
    monkeypatch.setattr(server.importlib.util, "find_spec", lambda name: object())

    config = server._uvicorn_config(_settings())

    assert config.http == "httptools"
    assert config.loop == "uvloop"
    assert config.proxy_headers is False
    assert config.limit_concurrency == 150


def test_uvicorn_config_has_portable_fallback(monkeypatch: Any) -> None:
    monkeypatch.setattr(server.importlib.util, "find_spec", lambda name: None)

    config = server._uvicorn_config(_settings(db_pool_size=2, db_max_overflow=1))

    assert config.http == "h11"
    assert config.loop == "asyncio"
    assert config.limit_concurrency == 64


def test_reap_replaces_failed_worker(monkeypatch: Any) -> None:
    supervisor = server.Supervisor(_settings(), 1)
    slot = supervisor.slots[0]
    slot.process = _DeadProcess()  # type: ignore[assignment]
    spawned: list[int] = []
    cleaned: list[int | None] = []
    monkeypatch.setattr(server.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(supervisor, "_spawn", lambda current: spawned.append(current.index))
    monkeypatch.setattr(supervisor, "_mark_worker_dead", cleaned.append)

    supervisor._reap()

    assert spawned == [0]
    assert cleaned == [123]
    assert slot.crashes == [100.0]


def test_reap_applies_backoff_to_crash_loop(monkeypatch: Any) -> None:
    supervisor = server.Supervisor(_settings(), 1)
    slot = supervisor.slots[0]
    slot.process = _DeadProcess()  # type: ignore[assignment]
    slot.crashes = [95.0, 96.0, 97.0, 98.0]
    spawned: list[int] = []
    monkeypatch.setattr(server.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(supervisor, "_spawn", lambda current: spawned.append(current.index))
    monkeypatch.setattr(supervisor, "_mark_worker_dead", lambda pid: None)

    supervisor._reap()

    assert not spawned
    assert slot.backoff_until == 102.0


def test_reap_retries_after_crash_window_expires(monkeypatch: Any) -> None:
    supervisor = server.Supervisor(_settings(), 1)
    slot = supervisor.slots[0]
    slot.crashes = [1.0, 2.0, 3.0, 4.0, 5.0]
    slot.backoff_until = 40.0
    spawned: list[int] = []
    monkeypatch.setattr(server.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(supervisor, "_spawn", lambda current: spawned.append(current.index))

    supervisor._reap()

    assert spawned == [0]
    assert slot.crashes == []
