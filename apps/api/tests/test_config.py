"""Тесты валидации конфигурации.

Главная проверяемая гарантия: приложение НЕ стартует с небезопасными
настройками в production.
"""

from __future__ import annotations

import pytest
from app.core.config import Settings
from pydantic import ValidationError as PydanticValidationError


def _prod(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "app_env": "production",
        "secret_key": "x" * 40,
        "postgres_password": "y" * 20,
        "public_url": "https://app.example.com",
        "tls_mode": "acme",
        "require_email_verification": False,
        "mail_transport": "smtp",
        "smtp_host": "smtp.example.com",
    }
    base.update(over)
    return base


class TestProductionSafety:
    def test_valid_production_config(self) -> None:
        settings = Settings(**_prod())  # type: ignore[arg-type]
        assert settings.is_production

    def test_placeholder_secret_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="SECRET_KEY"):
            Settings(**_prod(secret_key="CHANGE_ME"))  # type: ignore[arg-type]

    def test_short_secret_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Settings(**_prod(secret_key="tooshort"))  # type: ignore[arg-type]

    def test_http_public_url_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="PUBLIC_URL"):
            Settings(**_prod(public_url="http://app.example.com"))  # type: ignore[arg-type]

    def test_docs_enabled_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="ENABLE_API_DOCS"):
            Settings(**_prod(enable_api_docs=True))  # type: ignore[arg-type]

    def test_console_mail_with_verification_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Settings(**_prod(require_email_verification=True, mail_transport="console"))  # type: ignore[arg-type]

    def test_console_mail_rejected_outside_development(self) -> None:
        with pytest.raises(PydanticValidationError, match="MAIL_TRANSPORT=console"):
            Settings(**_prod(mail_transport="console"))  # type: ignore[arg-type]

    def test_staging_requires_real_secrets(self) -> None:
        with pytest.raises(PydanticValidationError, match="SECRET_KEY"):
            Settings(
                app_env="staging",
                secret_key="CHANGE_ME",
                postgres_password="valid-database-password",
                public_url="https://staging.example.com",
                mail_transport="smtp",
                smtp_host="smtp.example.com",
            )

    def test_plaintext_smtp_rejected_outside_development(self) -> None:
        with pytest.raises(PydanticValidationError, match="SMTP_SECURITY"):
            Settings(**_prod(smtp_security="none"))  # type: ignore[arg-type]

    def test_tls_none_without_proxies_rejected(self) -> None:
        with pytest.raises(PydanticValidationError, match="TRUSTED_PROXIES"):
            Settings(**_prod(tls_mode="none", trusted_proxies="", public_url="http://x"))  # type: ignore[arg-type]


class TestDevelopmentLenient:
    def test_dev_allows_empty_secret(self) -> None:
        # В разработке пустой SECRET_KEY допустим - генерируется заглушка.
        settings = Settings(app_env="development")
        assert settings.secret_bytes  # не пусто

    def test_dev_cors_includes_vite(self) -> None:
        settings = Settings(app_env="development", public_url="http://localhost:5173")
        assert any("5173" in o for o in settings.cors_origins)


class TestDerived:
    def test_previous_secret_keys_are_deduplicated(self) -> None:
        settings = Settings(
            app_env="development",
            secret_key="current-key",
            secret_key_previous="old-key,current-key,old-key",
        )
        assert settings.secret_materials == (b"current-key", b"old-key")

    def test_invalid_network_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Settings(app_env="development", trusted_proxies="not-a-network")

    def test_invalid_rate_limit_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            Settings(app_env="development", rate_limit_auth_per_min=0)

    def test_database_credentials_are_fully_url_encoded(self) -> None:
        settings = Settings(
            app_env="development",
            postgres_user="user/name",
            postgres_password="p@ss/word",
            postgres_db="db/name",
        )
        assert "user%2Fname:p%40ss%2Fword" in settings.database_url
        assert settings.database_url.endswith("/db%2Fname")

    def test_cookie_prefix_stripped_on_http(self) -> None:
        # По http префикс __Host- недопустим - приложение его снимает.
        settings = Settings(
            app_env="development",
            public_url="http://localhost:5173",
            session_cookie_name="__Host-sid",
        )
        assert not settings.cookie_name.startswith("__Host-")

    def test_cookie_prefix_kept_on_https(self) -> None:
        settings = Settings(**_prod(session_cookie_name="__Host-sid"))  # type: ignore[arg-type]
        assert settings.cookie_name == "__Host-sid"

    def test_derived_keys_are_independent(self) -> None:
        settings = Settings(app_env="development", secret_key="z" * 40)
        assert settings.derived_key("csrf") != settings.derived_key("totp-enc")

    def test_worker_count_auto(self) -> None:
        settings = Settings(app_env="development", api_workers=0)
        assert 1 <= settings.worker_count <= 8
