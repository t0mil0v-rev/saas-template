"""Общие фикстуры тестов.

Тесты здесь не требуют поднятой БД: они проверяют чистую логику
(криптографию, разбор прокси, лимитер, конфигурацию). Для интеграционных
тестов с реальной БД поднимите стек через docker-compose.dev.yml и
подключите отдельную тестовую базу.
"""

from __future__ import annotations

import os

import pytest

# Значения окружения для тестов ставим ДО импорта app.core.config, иначе
# сработает production-валидация с настоящими требованиями к секретам.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only-not-production")
os.environ.setdefault("POSTGRES_PASSWORD", "test-password")


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    """Сбрасываем кэш настроек между тестами, меняющими окружение."""
    from app.core.config import get_settings

    get_settings.cache_clear()
