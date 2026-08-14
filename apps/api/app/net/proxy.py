"""Определение реального IP клиента за обратным прокси.

Это не удобство, а основа безопасности. На IP завязаны лимиты частоты
запросов, блокировка перебора паролей, антиспам отзывов и аудит. Если IP
определяется неправильно, все эти механизмы либо не работают вовсе, либо
работают против добросовестных пользователей.

Типовая ошибка - брать первый элемент ``X-Forwarded-For``. Заголовок целиком
контролируется клиентом: любой может послать ``X-Forwarded-For: 1.2.3.4``
и обойти лимиты, подставив в логи чужой адрес. Здесь реализован
единственный корректный подход:

1. Доверять заголовкам ``X-Forwarded-*`` только если TCP-пир входит в
   ``TRUSTED_PROXIES``. Иначе - использовать адрес самого соединения.
2. Брать не первый, а ``depth``-й справа элемент цепочки. Справа
   заголовок дописывают доверенные прокси, и подделать эти позиции
   клиент не может.
3. Пустой ``TRUSTED_PROXIES`` = заголовки игнорируются полностью
   (правильный режим, когда API доступен напрямую).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from starlette.requests import HTTPConnection

from app.core.config import Settings

_UNKNOWN = "0.0.0.0"  # noqa: S104 - заглушка для случая, когда пира нет (тесты, unix-сокет)


@dataclass(frozen=True, slots=True)
class ClientInfo:
    """Результат разбора сетевого происхождения запроса."""

    ip: str
    """Реальный IP клиента - на него вешаются лимиты и аудит."""

    peer_ip: str
    """Адрес TCP-соединения (прокси или сам клиент)."""

    via_trusted_proxy: bool
    """True, если запрос пришёл от доверенного прокси."""

    scheme: str
    """http или https с точки зрения клиента (с учётом X-Forwarded-Proto)."""

    forwarded_chain: tuple[str, ...]
    """Разобранная цепочка X-Forwarded-For, как её прислали."""


def _parse_ip(value: str) -> str | None:
    """Нормализует элемент XFF в строку IP или возвращает None."""
    value = value.strip()
    if not value:
        return None
    # Формы: "1.2.3.4", "1.2.3.4:5678", "[::1]", "[::1]:443"
    if value.startswith("["):
        value = value.partition("]")[0].lstrip("[")
    elif value.count(":") == 1:
        value = value.partition(":")[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _is_trusted(ip: str, settings: Settings) -> bool:
    networks = settings.trusted_proxy_networks
    if not networks:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def resolve_client(conn: HTTPConnection, settings: Settings) -> ClientInfo:
    """Определяет реального клиента по соединению и заголовкам."""
    peer_ip = conn.client.host if conn.client else _UNKNOWN
    scheme = conn.url.scheme

    trusted = _is_trusted(peer_ip, settings)
    if not trusted:
        # Заголовкам не верим вообще: либо прокси не настроен, либо кто-то
        # обращается к API в обход него.
        return ClientInfo(
            ip=peer_ip,
            peer_ip=peer_ip,
            via_trusted_proxy=False,
            scheme=scheme,
            forwarded_chain=(),
        )

    raw_xff = conn.headers.get("x-forwarded-for", "")
    chain = tuple(p for p in (_parse_ip(x) for x in raw_xff.split(",")) if p)

    depth = max(1, settings.trusted_proxy_depth)
    if len(chain) >= depth:
        client_ip = chain[-depth]
    elif chain:
        # При неверном depth нельзя брать элемент, который мог добавить клиент.
        # Адрес доверенного TCP-пира менее точен, зато не подделывается снаружи.
        client_ip = peer_ip
    else:
        client_ip = peer_ip

    forwarded_proto = conn.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if forwarded_proto in ("http", "https"):
        scheme = forwarded_proto

    return ClientInfo(
        ip=client_ip,
        peer_ip=peer_ip,
        via_trusted_proxy=True,
        scheme=scheme,
        forwarded_chain=chain,
    )
