"""Конфигурация приложения.

Единственный источник правды о настройках. Читается из переменных окружения
(и файла .env при локальной разработке), валидируется на старте.

Ключевой принцип: приложение отказывается стартовать с небезопасной
конфигурацией в production. Лучше упасть при деплое с внятной ошибкой,
чем месяц работать с ключом подписи "CHANGE_ME".
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import multiprocessing
import os
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Значения, которые обязаны быть заменены перед выкладкой в production.
_PLACEHOLDERS = frozenset({"", "change_me", "changeme", "secret", "password", "todo", "xxx"})

Environment = Literal["development", "staging", "production"]
TLSMode = Literal["acme", "internal", "none"]
MailTransport = Literal["smtp", "console"]
DBSSLMode = Literal["disable", "prefer", "require", "verify-ca", "verify-full"]


def _hkdf_sha256(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF (RFC 5869) на SHA-256.

    Нужен, чтобы из одного SECRET_KEY получить несколько независимых ключей
    (подпись CSRF, шифрование TOTP-секретов, хеширование IP). Компрометация
    одного производного ключа не раскрывает остальные и не раскрывает мастер-ключ.
    """
    prk = hmac.new(b"\x00" * 32, ikm, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


class Settings(BaseSettings):
    """Полная конфигурация. Имена полей совпадают с именами переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # базовое
    app_name: str = "SaaS Platform"
    app_env: Environment = "production"
    public_url: str = "http://localhost:5173"
    domain: str = "localhost"

    # TLS
    tls_mode: TLSMode = "acme"

    # секреты
    secret_key: str = Field(default="", repr=False)
    # Предыдущие master keys через запятую. Нужны только на время ротации,
    # пока сохранённые TOTP-секреты не перешифрованы новым ключом.
    secret_key_previous: str = Field(default="", repr=False)

    # база данных
    postgres_user: str = "saas"
    postgres_password: str = Field(default="", repr=False)
    postgres_db: str = "saas"
    postgres_host: str = "postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65_535)
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_pool_timeout: int = Field(default=10, ge=1, le=300)
    db_statement_timeout_ms: int = Field(default=15_000, ge=100, le=600_000)
    db_sslmode: DBSSLMode = "prefer"

    # redis
    redis_url: str = Field(default="", repr=False)

    # сеть
    api_host: str = "0.0.0.0"  # noqa: S104 - слушаем внутри контейнера, наружу пускает только Caddy
    api_port: int = Field(default=8000, ge=1, le=65_535)
    api_workers: int = Field(default=0, ge=0, le=64)
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024, le=104_857_600)
    request_timeout_s: int = Field(default=30, ge=1, le=3600)
    keepalive_timeout_s: int = Field(default=15, ge=1, le=300)
    graceful_shutdown_s: int = Field(default=25, ge=1, le=600)
    listen_backlog: int = Field(default=2048, ge=64, le=65_535)
    max_requests_per_worker: int = Field(default=0, ge=0)
    trusted_proxies: str = ""
    trusted_proxy_depth: int = Field(default=1, ge=1, le=16)

    # сессии и аутентификация
    session_cookie_name: str = "__Host-sid"
    session_absolute_ttl_h: int = Field(default=720, ge=1, le=8760)
    session_idle_ttl_h: int = Field(default=72, ge=1, le=8760)
    session_max_per_user: int = Field(default=10, ge=1, le=100)
    require_email_verification: bool = True
    allow_public_signup: bool = True
    login_max_failures: int = Field(default=8, ge=1, le=100)
    login_lockout_minutes: int = Field(default=15, ge=1, le=10_080)
    password_min_length: int = Field(default=12, ge=8, le=128)

    # ограничение частоты
    rate_limit_enabled: bool = True
    rate_limit_global_per_min: int = Field(default=300, ge=1, le=1_000_000)
    rate_limit_auth_per_min: int = Field(default=10, ge=1, le=100_000)
    rate_limit_review_per_hour: int = Field(default=5, ge=1, le=100_000)

    # почта
    mail_transport: MailTransport = "console"
    mail_from: str = "SaaS Platform <no-reply@localhost>"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_user: str = ""
    smtp_password: str = Field(default="", repr=False)
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_timeout_s: int = 10
    mail_outbox_poll_s: float = Field(default=1.0, gt=0, le=60)
    mail_outbox_lease_s: int = Field(default=300, ge=30, le=3600)
    mail_outbox_max_attempts: int = Field(default=8, ge=1, le=100)
    mail_outbox_retention_days: int = Field(default=7, ge=1, le=365)

    # наблюдаемость
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    log_format: Literal["json", "console"] = "json"
    metrics_enabled: bool = True
    metrics_allowed_nets: str = "127.0.0.1/32"
    enable_api_docs: bool = False
    audit_retention_days: int = Field(default=365, ge=30, le=3650)
    session_retention_days: int = Field(default=7, ge=1, le=365)
    invitation_retention_days: int = Field(default=30, ge=1, le=365)

    # первичный администратор
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = Field(default="", repr=False)
    bootstrap_org_name: str = "Acme"
    bootstrap_org_slug: str = "acme"

    # Валидаторы

    @field_validator("public_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        value = v.rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("PUBLIC_URL должен быть абсолютным http(s)-адресом")
        return value

    @field_validator("password_min_length")
    @classmethod
    def _min_password_length(cls, v: int) -> int:
        # NIST SP 800-63B: минимум 8. Мы держим планку выше, но не ниже 8 никогда.
        if v < 8:
            raise ValueError("PASSWORD_MIN_LENGTH не может быть меньше 8")
        return v

    @field_validator("trusted_proxies", "metrics_allowed_nets")
    @classmethod
    def _validate_networks(cls, v: str) -> str:
        """Проверяем CIDR прямо на старте - опечатка в проде опаснее краша."""
        for item in (p.strip() for p in v.split(",")):
            if not item:
                continue
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ValueError(f"некорректная сеть {item!r}: {exc}") from exc
        return v

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Settings:
        """Набор жёстких требований для развёрнутых окружений."""
        # Console-транспорт намеренно печатает тело письма, то есть рабочие
        # ссылки с токенами. Это удобно только в локальной разработке; staging
        # часто содержит реальные учётные записи и не должен утекать в логи.
        if self.app_env == "development":
            return self

        problems: list[str] = []

        def _weak(name: str, value: str, min_len: int = 32) -> None:
            if value.strip().lower() in _PLACEHOLDERS:
                problems.append(f"{name}: осталось значение-заглушка")
            elif len(value) < min_len:
                problems.append(f"{name}: длина {len(value)} < {min_len} символов")

        _weak("SECRET_KEY", self.secret_key, 32)
        _weak("POSTGRES_PASSWORD", self.postgres_password, 16)

        if not self.public_url.startswith("https://"):
            problems.append("PUBLIC_URL должен использовать https://")
        if self.app_env == "production" and self.enable_api_docs:
            problems.append(
                "ENABLE_API_DOCS=true в production раскрывает всю схему API - выключите"
            )
        if self.mail_transport == "console":
            problems.append(
                "MAIL_TRANSPORT=console разрешён только в development; "
                "для staging/production настройте SMTP"
            )
        if self.mail_transport == "smtp" and not self.smtp_host:
            problems.append("MAIL_TRANSPORT=smtp, но SMTP_HOST не задан")
        if self.mail_transport == "smtp" and self.smtp_security == "none":
            problems.append("SMTP_SECURITY=none запрещён вне development")
        if self.tls_mode == "none" and not self.trusted_proxies:
            problems.append(
                "TLS_MODE=none подразумевает внешний балансировщик, "
                "но TRUSTED_PROXIES пуст - реальный IP клиента определить нельзя"
            )
        if (
            self.bootstrap_admin_password
            and self.bootstrap_admin_password.strip().lower() in _PLACEHOLDERS
        ):
            problems.append("BOOTSTRAP_ADMIN_PASSWORD: осталось значение-заглушка")

        if problems:
            listing = "\n".join(f"  - {p}" for p in problems)
            raise ValueError(
                f"Небезопасная конфигурация для APP_ENV={self.app_env}:\n"
                f"{listing}\n"
                "Исправьте .env (подсказка: ./deploy/scripts/gen-secrets.sh) и повторите запуск."
            )
        return self

    # Производные значения

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def secret_bytes(self) -> bytes:
        # В development допускаем пустой SECRET_KEY: генерируем детерминированный
        # ключ-заглушку, чтобы разработчику не приходилось ничего настраивать.
        if not self.secret_key:
            return hashlib.sha256(b"insecure-development-key").digest()
        return self.secret_key.encode()

    def derived_key(self, purpose: str, length: int = 32) -> bytes:
        """Независимый ключ для конкретной задачи, выведенный из SECRET_KEY."""
        return _hkdf_sha256(self.secret_bytes, f"saas/v1/{purpose}".encode(), length)

    @property
    def secret_materials(self) -> tuple[bytes, ...]:
        """Текущий и предыдущие master keys без дублей."""
        values = [self.secret_bytes]
        values.extend(
            item.strip().encode() for item in self.secret_key_previous.split(",") if item.strip()
        )
        return tuple(dict.fromkeys(values))

    def derived_keys(self, purpose: str, length: int = 32) -> tuple[bytes, ...]:
        info = f"saas/v1/{purpose}".encode()
        return tuple(_hkdf_sha256(material, info, length) for material in self.secret_materials)

    @property
    def database_url(self) -> str:
        """DSN для SQLAlchemy + asyncpg."""
        from urllib.parse import quote

        return (
            f"postgresql+asyncpg://{quote(self.postgres_user, safe='')}:"
            f"{quote(self.postgres_password, safe='')}@{self.postgres_host}:{self.postgres_port}/"
            f"{quote(self.postgres_db, safe='')}"
        )

    @property
    def sync_database_url(self) -> str:
        """DSN для Alembic (миграции идут синхронным драйвером)."""
        return self.database_url.replace("+asyncpg", "")

    @property
    def worker_count(self) -> int:
        """Число воркеров. 0 = авто, но не больше 8: дальше растёт только
        нагрузка на пул соединений, а не пропускная способность."""
        if self.api_workers > 0:
            return self.api_workers
        return max(1, min(8, (os.cpu_count() or multiprocessing.cpu_count() or 1)))

    @property
    def trusted_proxy_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(
            ipaddress.ip_network(p.strip(), strict=False)
            for p in self.trusted_proxies.split(",")
            if p.strip()
        )

    @property
    def metrics_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(
            ipaddress.ip_network(p.strip(), strict=False)
            for p in self.metrics_allowed_nets.split(",")
            if p.strip()
        )

    @property
    def secure_cookies(self) -> bool:
        """Ставить ли флаг Secure. Только для https-происхождения."""
        return self.public_url.startswith("https://")

    @property
    def cookie_name(self) -> str:
        """Префикс __Host- требует Secure + Path=/ + отсутствие Domain.

        По http браузер такой cookie просто отбросит, поэтому в локальной
        разработке префикс снимаем - иначе логин молча не работает.
        """
        name = self.session_cookie_name
        if not self.secure_cookies and name.startswith(("__Host-", "__Secure-")):
            return name.split("-", 1)[1]
        return name

    @property
    def csrf_cookie_name(self) -> str:
        return f"{self.cookie_name}-csrf"

    @property
    def cors_origins(self) -> tuple[str, ...]:
        """Разрешённые Origin. Строго список, никаких «*».

        В development добавляем Vite dev-server, иначе фронтенд на :5173
        не сможет ходить в API на :8000.
        """
        origins = {self.public_url}
        if not self.is_production:
            origins.update({"http://localhost:5173", "http://127.0.0.1:5173"})
        return tuple(sorted(o for o in origins if o))

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).hostname or self.domain


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Настройки-синглтон. Кэш сбрасывается только в тестах."""
    return Settings()
