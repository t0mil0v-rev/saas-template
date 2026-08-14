"""Тесты криптографического ядра - самой чувствительной к ошибкам части."""

from __future__ import annotations

import base64
import secrets
import uuid

from app.core import security
from app.core.config import get_settings
from app.db.models import User
from app.services.auth import AuthService
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class TestPasswordHashing:
    def test_hash_verify_roundtrip(self) -> None:
        h = security.hash_password("correct horse battery staple")
        assert security.verify_password(h, "correct horse battery staple")

    def test_wrong_password_rejected(self) -> None:
        h = security.hash_password("s3cret-passphrase-long")
        assert not security.verify_password(h, "wrong")

    def test_hash_is_salted(self) -> None:
        # Один пароль → разные хеши (соль). Иначе одинаковые пароли видны в дампе.
        a = security.hash_password("same-password-here")
        b = security.hash_password("same-password-here")
        assert a != b

    def test_verify_survives_garbage_hash(self) -> None:
        # Битый хеш в БД не должен ронять вход - только возвращать «не совпало».
        assert not security.verify_password("not-a-valid-hash", "whatever")

    def test_unicode_normalized(self) -> None:
        # NFC и NFD одной строки должны совпасть после нормализации.
        nfc = "café-password-123"
        nfd = "café-password-123"
        h = security.hash_password(nfc)
        assert security.verify_password(h, nfd)


class TestPasswordPolicy:
    def test_too_short_rejected(self) -> None:
        assert not security.check_password_policy("short").ok

    def test_common_password_rejected(self) -> None:
        assert not security.check_password_policy("password").ok
        assert not security.check_password_policy("123456789012").ok

    def test_contains_email_rejected(self) -> None:
        res = security.check_password_policy("johndoe-supersecret", email="johndoe@x.com")
        assert not res.ok

    def test_low_variety_rejected(self) -> None:
        assert not security.check_password_policy("aaaaaaaaaaaaaa").ok

    def test_good_passphrase_accepted(self) -> None:
        assert security.check_password_policy("correct-horse-battery-staple").ok

    def test_overlong_rejected(self) -> None:
        assert not security.check_password_policy("x" * 200).ok


class TestTokens:
    def test_token_hash_is_one_way(self) -> None:
        raw, hashed = security.generate_token()
        assert raw != hashed
        assert security.hash_token(raw) == hashed

    def test_tokens_are_unique(self) -> None:
        tokens = {security.generate_token()[0] for _ in range(100)}
        assert len(tokens) == 100

    def test_constant_time_equals(self) -> None:
        assert security.constant_time_equals("abc", "abc")
        assert not security.constant_time_equals("abc", "abd")


class TestCsrf:
    def test_valid_token_accepted(self) -> None:
        session_hash = "a" * 64
        token = security.issue_csrf_token(session_hash)
        assert security.verify_csrf_token(token, session_hash)

    def test_token_bound_to_session(self) -> None:
        # Токен, выписанный для одной сессии, не подходит к другой.
        token = security.issue_csrf_token("a" * 64)
        assert not security.verify_csrf_token(token, "b" * 64)

    def test_garbage_token_rejected(self) -> None:
        assert not security.verify_csrf_token("garbage!!!", "a" * 64)
        assert not security.verify_csrf_token("", "a" * 64)


class TestTotp:
    def test_secret_roundtrip_encryption(self) -> None:
        secret = security.generate_totp_secret()
        blob = security.encrypt_totp_secret(secret, "user-123")
        assert blob != secret
        assert security.decrypt_totp_secret(blob, "user-123") == secret

    def test_secret_bound_to_user(self) -> None:
        # Зашифрованный секрет нельзя перенести в чужую учётную запись.
        secret = security.generate_totp_secret()
        blob = security.encrypt_totp_secret(secret, "user-123")
        assert security.decrypt_totp_secret(blob, "user-999") is None

    def test_secret_survives_key_rotation(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("SECRET_KEY", "old-test-key-with-enough-entropy-123456")
        get_settings.cache_clear()
        secret = security.generate_totp_secret()
        blob = security.encrypt_totp_secret(secret, "user-123")

        monkeypatch.setenv("SECRET_KEY", "new-test-key-with-enough-entropy-654321")
        monkeypatch.setenv("SECRET_KEY_PREVIOUS", "old-test-key-with-enough-entropy-123456")
        get_settings.cache_clear()

        assert security.decrypt_totp_secret(blob, "user-123") == secret

    def test_legacy_secret_remains_readable(self) -> None:
        secret = security.generate_totp_secret()
        key = get_settings().derived_key("totp-enc")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(nonce, secret.encode(), b"user-123")
        legacy_blob = base64.b64encode(nonce + ciphertext).decode()

        assert security.decrypt_totp_secret(legacy_blob, "user-123") == secret

    def test_totp_verification(self) -> None:
        import pyotp

        secret = security.generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        assert security.verify_totp(secret, code)
        assert not security.verify_totp(secret, "000000")

    def test_match_returns_replay_step(self) -> None:
        import pyotp

        secret = security.generate_totp_secret()
        timestamp = 1_800_000_000.0
        code = pyotp.TOTP(secret).at(timestamp)

        assert security.match_totp_step(secret, code, at_time=timestamp) == int(
            timestamp // security.TOTP_INTERVAL
        )

    def test_same_totp_step_cannot_be_reused(self) -> None:
        import pyotp

        user_id = uuid.uuid4()
        secret = security.generate_totp_secret()
        user = User(
            id=user_id,
            email="totp@example.com",
            password_hash="unused",
            totp_enabled=True,
            totp_secret_enc=security.encrypt_totp_secret(secret, str(user_id)),
            totp_last_step=None,
        )
        code = pyotp.TOTP(secret).now()

        assert AuthService._accept_totp(user, code)
        assert not AuthService._accept_totp(user, code)

    def test_malformed_code_rejected(self) -> None:
        secret = security.generate_totp_secret()
        assert not security.verify_totp(secret, "abc")
        assert not security.verify_totp(secret, "12345")


class TestRecoveryCodes:
    def test_generate_and_hash(self) -> None:
        codes = security.generate_recovery_codes()
        assert len(codes) == security.RECOVERY_CODE_COUNT
        # Хеш нечувствителен к регистру и дефисам (пользователь может ошибиться).
        h1 = security.hash_recovery_code(codes[0])
        h2 = security.hash_recovery_code(codes[0].upper().replace("-", ""))
        assert h1 == h2
        assert len(codes[0].replace("-", "")) == security.RECOVERY_CODE_BYTES * 2

    def test_hash_is_keyed(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        code = "01234-56789-abcde-f0123"
        first = security.hash_recovery_code(code)
        monkeypatch.setenv("SECRET_KEY", "another-test-master-key-with-32-bytes")
        get_settings.cache_clear()

        assert security.hash_recovery_code(code) != first


class TestIpHashing:
    def test_same_ip_same_hash(self) -> None:
        assert security.hash_ip("192.0.2.1") == security.hash_ip("192.0.2.1")

    def test_different_ip_different_hash(self) -> None:
        assert security.hash_ip("192.0.2.1") != security.hash_ip("192.0.2.2")

    def test_hash_is_not_reversible_length(self) -> None:
        # Хеш фиксированной длины и не содержит исходного адреса.
        h = security.hash_ip("192.0.2.1")
        assert "192.0.2.1" not in h
        assert len(h) == 32
