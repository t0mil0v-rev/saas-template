"""Структурированное логирование.

Один формат для всего: приложение, uvicorn и SQLAlchemy пишут через structlog.
В production - JSON (его ест journald/Loki/ELK без парсеров), в разработке -
цветной человекочитаемый вывод.

Отдельная забота - не залогировать секрет. Процессор `_redact` вырезает
значения по списку опасных ключей на всех уровнях вложенности.
"""

from __future__ import annotations

import logging
import logging.config
import sys
from contextvars import ContextVar
from typing import Any, cast

import structlog

# Идентификатор запроса живёт в contextvar: попадает в каждую запись лога,
# сделанную во время обработки этого запроса, без ручной передачи по цепочке.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
client_ip_var: ContextVar[str] = ContextVar("client_ip", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")

# Ключи, значение которых никогда не должно попасть в лог.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "secret",
        "secret_key",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "csrf_token",
        "authorization",
        "cookie",
        "set-cookie",
        "totp_secret",
        "totp_code",
        "recovery_code",
        "api_key",
        "postgres_password",
        "smtp_password",
    }
)
_REDACTED = "[redacted]"


def _redact(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивно заменяет значения чувствительных ключей."""

    def walk(value: Any, depth: int = 0) -> Any:
        if depth > 6:  # защита от циклов и монструозных структур
            return "[max-depth]"
        if isinstance(value, dict):
            return {
                k: (_REDACTED if str(k).lower() in _SENSITIVE_KEYS else walk(v, depth + 1))
                for k, v in value.items()
            }
        if isinstance(value, list | tuple):
            return type(value)(walk(v, depth + 1) for v in value)
        return value

    return cast(dict[str, Any], walk(event_dict))


def _bind_context(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Добавляет request_id / ip / user_id из contextvars."""
    event_dict.setdefault("request_id", request_id_var.get())
    ip = client_ip_var.get()
    if ip != "-":
        event_dict.setdefault("client_ip", ip)
    uid = user_id_var.get()
    if uid != "-":
        event_dict.setdefault("user_id", uid)
    return event_dict


def configure_logging(level: str = "info", fmt: str = "json") -> None:
    """Настраивает structlog и перенаправляет в него stdlib-логгеры."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _bind_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _redact,
    ]

    if fmt == "json":
        renderer: Any = structlog.processors.JSONRenderer(ensure_ascii=False)
        # Полный traceback как строка - иначе JSON-строка ломается о переводы строк
        shared.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # uvicorn ведёт собственный access-log - он дублирует наш middleware
    # и не знает про реальный IP за прокси. Выключаем.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    # SQLAlchemy болтлив на INFO; поднимаем порог, кроме отладки.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if log_level <= logging.DEBUG else logging.WARNING
    )
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str = "app") -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
