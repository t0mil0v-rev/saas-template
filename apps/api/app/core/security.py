"""Криптографическое ядро.

Здесь собрано всё, что имеет отношение к секретам: хеширование паролей,
одноразовые токены, CSRF, шифрование TOTP-секретов, обезличивание IP.
Ни один модуль выше по стеку не должен вызывать hashlib/secrets напрямую.

Принятые решения и их причины:

* Argon2id для паролей. Устойчив и к GPU-, и к side-channel-атакам.
  Параметры вынесены в константы и проверяются тестом.
* Opaque-токены сессий, а не JWT. Сессию можно отозвать мгновенно -
  достаточно удалить строку в БД. JWT отзывается только чёрным списком,
  то есть тем же походом в БД, но с лишней криптографией и риском
  ошибок в валидации alg/aud/exp.
* В БД лежит только хеш любого токена (сессия, сброс пароля,
  приглашение). Дамп базы не даёт войти ни в один аккаунт.
* Сравнение всегда постоянного времени - ``hmac.compare_digest``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
import unicodedata
from dataclasses import dataclass

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

# Пароли

# Параметры по рекомендациям OWASP Password Storage Cheat Sheet с запасом.
# 64 МиБ × parallelism 2 - примерно 60-100 мс на современном ядре: достаточно
# дорого для перебора и незаметно для одного входа.
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_KIB = 65536  # 64 МиБ
_ARGON2_PARALLELISM = 2
_ARGON2_HASH_LEN = 32
_ARGON2_SALT_LEN = 16

_hasher = PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_KIB,
    parallelism=_ARGON2_PARALLELISM,
    hash_len=_ARGON2_HASH_LEN,
    salt_len=_ARGON2_SALT_LEN,
    type=Type.ID,
)

# Пароль длиннее этого отвергаем: Argon2 честно захеширует и мегабайт,
# а злоумышленник получит дешёвый способ сжечь CPU сервера.
MAX_PASSWORD_LENGTH = 128

# Мини-словарь самых частых паролей. Полноценную проверку по утечкам
# подключайте отдельно (локальная копия HIBP-хешей - без обращений наружу).
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "passw0rd",
        "password1",
        "qwerty",
        "qwerty123",
        "123456",
        "12345678",
        "123456789",
        "1234567890",
        "123456789012",
        "111111",
        "000000",
        "iloveyou",
        "admin",
        "administrator",
        "root",
        "toor",
        "letmein",
        "welcome",
        "monkey",
        "dragon",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "abc123",
        "changeme",
        "secret",
        "master",
        "superman",
        "trustno1",
        "qazwsx",
        "zaq12wsx",
        "654321",
        "photoshop",
        "starwars",
        "пароль",
        "йцукен",
        "1q2w3e4r",
        "1qaz2wsx",
    }
)


def normalize_password(password: str) -> str:
    """NFKC-нормализация по рекомендации NIST SP 800-63B §5.1.1.2.

    Без неё пароль, набранный с другой раскладкой Unicode (например, é как
    один символ против e + акцент), не совпадёт сам с собой.
    """
    return unicodedata.normalize("NFKC", password)


@dataclass(frozen=True, slots=True)
class PasswordCheck:
    ok: bool
    reason: str = ""


def check_password_policy(password: str, *, email: str = "") -> PasswordCheck:
    """Проверяет пароль на минимальные требования.

    Намеренно НЕ требует «спецсимвол + цифру + заглавную»: такие правила
    ведут к `Password1!` и запрету на нормальные парольные фразы.
    Требуем длину, отсутствие в словаре и непохожесть на собственный e-mail.
    """
    settings = get_settings()
    pwd = normalize_password(password)

    if len(pwd) < settings.password_min_length:
        return PasswordCheck(False, f"Пароль короче {settings.password_min_length} символов")
    if len(pwd) > MAX_PASSWORD_LENGTH:
        return PasswordCheck(False, f"Пароль длиннее {MAX_PASSWORD_LENGTH} символов")
    if pwd.lower() in _COMMON_PASSWORDS:
        return PasswordCheck(False, "Этот пароль слишком распространён")
    if len(set(pwd)) < 5:
        return PasswordCheck(False, "Пароль состоит из слишком малого числа разных символов")
    # Чисто цифровой пароль (любой длины) - это PIN, а не пароль: словарь
    # цифровых последовательностей мал и перебирается мгновенно. Отсекаем,
    # включая «123456789012» и телефоны/даты.
    if pwd.isdigit():
        return PasswordCheck(False, "Пароль не должен состоять только из цифр")
    # Длинная монотонная последовательность (клавиатурный ряд, счёт) -
    # низкая энтропия при формально достаточной длине.
    if _is_sequential(pwd):
        return PasswordCheck(False, "Пароль похож на простую последовательность символов")
    if email:
        local = email.split("@", 1)[0].lower()
        if len(local) >= 3 and local in pwd.lower():
            return PasswordCheck(False, "Пароль не должен содержать ваш e-mail")
    return PasswordCheck(True)


def _is_sequential(pwd: str, *, threshold: float = 0.7) -> bool:
    """True, если пароль преимущественно состоит из соседних по коду символов.

    Ловит «abcdefghijkl», «12345678», «qwerty…» набранное подряд и обратные
    последовательности. Порог 0.7 - доля соседних переходов среди всех.
    """
    low = pwd.lower()
    if len(low) < 4:
        return False
    steps = [ord(low[i + 1]) - ord(low[i]) for i in range(len(low) - 1)]
    monotone = sum(1 for s in steps if s in (1, -1))
    return monotone / len(steps) >= threshold


def hash_password(password: str) -> str:
    return _hasher.hash(normalize_password(password))


async def hash_password_async(password: str) -> str:
    """Выполняет CPU-bound Argon2 вне event loop."""
    return await asyncio.to_thread(hash_password, password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Проверка пароля. Любая ошибка разбора хеша трактуется как несовпадение."""
    try:
        return _hasher.verify(stored_hash, normalize_password(password))
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


async def verify_password_async(stored_hash: str, password: str) -> bool:
    return await asyncio.to_thread(verify_password, stored_hash, password)


def password_needs_rehash(stored_hash: str) -> bool:
    """True, если хеш сделан устаревшими параметрами.

    Вызывается после успешного входа: единственный момент, когда открытый
    пароль есть в памяти и его можно перехешировать бесплатно для пользователя.
    """
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return True


# Готовый валидный хеш для «холостой» проверки. Нужен, чтобы вход с
# несуществующим e-mail занимал столько же времени, сколько с существующим,
# и не позволял перечислять учётные записи по таймингу.
_DUMMY_HASH = _hasher.hash("dummy-password-for-constant-time-login")


def dummy_verify() -> None:
    """Сжигает столько же CPU, сколько настоящая проверка пароля."""
    try:
        _hasher.verify(_DUMMY_HASH, "wrong")
    except (VerifyMismatchError, InvalidHashError, ValueError):
        pass


async def dummy_verify_async() -> None:
    await asyncio.to_thread(dummy_verify)


# Одноразовые токены (сессии, сброс пароля, приглашения, верификация почты)

TOKEN_BYTES = 32  # 256 бит энтропии


def generate_token() -> tuple[str, str]:
    """Возвращает пару (открытый токен для клиента, хеш для хранения в БД).

    Открытый токен не восстановим из хеша, поэтому утечка БД не даёт войти.
    SHA-256 без соли здесь достаточно и намеренно: токен уже случайный на
    256 бит, перебирать нечего, а быстрый хеш нужен для проверки на каждом
    запросе.
    """
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# CSRF: подписанный double-submit cookie
#
#  Обычный double-submit ломается, если атакующий контролирует поддомен:
#  он выставляет свою cookie на *.example.com и подставляет тот же
#  заголовок. Поэтому токен подписывается ключом сервера и привязывается
#  к конкретной сессии - подделать пару «cookie + заголовок» нельзя.


def issue_csrf_token(session_token_hash: str) -> str:
    nonce = secrets.token_bytes(16)
    mac = _csrf_mac(nonce, session_token_hash)
    return base64.urlsafe_b64encode(nonce + mac).decode().rstrip("=")


def verify_csrf_token(token: str, session_token_hash: str) -> bool:
    try:
        padded = token + "=" * (-len(token) % 4)
        blob = base64.urlsafe_b64decode(padded.encode())
    except (ValueError, TypeError):
        return False
    if len(blob) != 32:
        return False
    nonce, mac = blob[:16], blob[16:]
    return hmac.compare_digest(mac, _csrf_mac(nonce, session_token_hash))


def _csrf_mac(nonce: bytes, session_token_hash: str) -> bytes:
    key = get_settings().derived_key("csrf")
    return hmac.new(key, nonce + session_token_hash.encode(), hashlib.sha256).digest()[:16]


# TOTP (двухфакторная аутентификация)

TOTP_DIGITS = 6
TOTP_INTERVAL = 30
# Принимаем код из соседнего окна: часы на телефоне пользователя редко
# идеальны. Одно окно = ±30 c. Больше - заметно расширяет окно атаки.
TOTP_VALID_WINDOW = 1


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, email: str, issuer: str) -> str:
    """otpauth://-ссылка для QR-кода. QR рисуется на клиенте, наружу не ходим."""
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL).provisioning_uri(
        name=email, issuer_name=issuer
    )


def match_totp_step(secret: str, code: str, *, at_time: float | None = None) -> int | None:
    """Возвращает принятый временной шаг, чтобы запретить повтор кода."""
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != TOTP_DIGITS:
        return None
    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)
    current = int((at_time if at_time is not None else time.time()) // TOTP_INTERVAL)
    offsets = [0]
    for distance in range(1, TOTP_VALID_WINDOW + 1):
        offsets.extend((-distance, distance))
    for offset in offsets:
        step = current + offset
        if step >= 0 and hmac.compare_digest(totp.at(step * TOTP_INTERVAL), code):
            return step
    return None


def verify_totp(secret: str, code: str) -> bool:
    return match_totp_step(secret, code) is not None


def encrypt_totp_secret(secret: str, user_id: str) -> str:
    """Шифрует TOTP-секрет для хранения в БД (AES-256-GCM).

    ``user_id`` идёт как associated data: строку нельзя перенести в чужую
    запись - расшифровка чужой строки провалится по тегу аутентичности.
    """
    key = get_settings().derived_key("totp-enc")
    key_id = hashlib.sha256(key).hexdigest()[:12]
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, secret.encode(), user_id.encode())
    payload = base64.b64encode(nonce + ct).decode()
    return f"v1.{key_id}.{payload}"


def decrypt_totp_secret(blob: str, user_id: str) -> str | None:
    keys = get_settings().derived_keys("totp-enc")
    payload = blob
    if blob.startswith("v1."):
        try:
            _, key_id, payload = blob.split(".", 2)
        except ValueError:
            return None
        keys = tuple(key for key in keys if hashlib.sha256(key).hexdigest()[:12] == key_id)

    try:
        data = base64.b64decode(payload.encode(), validate=True)
    except (ValueError, TypeError):
        return None

    for key in keys:
        try:
            return AESGCM(key).decrypt(data[:12], data[12:], user_id.encode()).decode()
        except Exception:  # noqa: S112 - пробуем следующий ключ из keyring
            continue
    return None


# Резервные коды 2FA

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_BYTES = 10


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Создаёт коды с 80 битами случайности и короткими группами для ввода."""
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(RECOVERY_CODE_BYTES)
        codes.append("-".join(raw[index : index + 5] for index in range(0, len(raw), 5)))
    return codes


def hash_recovery_code(code: str) -> str:
    normalized = code.strip().lower().replace("-", "").encode()
    key = get_settings().derived_key("recovery-code")
    return hmac.new(key, normalized, hashlib.sha256).hexdigest()


def recovery_code_hash_candidates(code: str) -> tuple[str, ...]:
    """Хеши для текущего/старых ключей плюс legacy SHA-256 на время миграции."""
    normalized = code.strip().lower().replace("-", "").encode()
    candidates = [
        hmac.new(key, normalized, hashlib.sha256).hexdigest()
        for key in get_settings().derived_keys("recovery-code")
    ]
    candidates.append(hashlib.sha256(normalized).hexdigest())
    return tuple(dict.fromkeys(candidates))


# Обезличивание IP


def hash_ip(ip: str) -> str:
    """Хеш IP для антиспама и аудита.

    Хранить сырые IP дольше необходимого - лишний риск и лишние обязанности
    по 152-ФЗ/GDPR. HMAC с серверным ключом позволяет сравнивать «тот же
    это адрес или нет», но не восстановить адрес из дампа базы.
    """
    key = get_settings().derived_key("ip-hash")
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()[:32]


def hash_email_for_log(email: str) -> str:
    """Стабильная метка получателя без адреса в production-логах."""
    key = get_settings().derived_key("email-log-hash")
    return hmac.new(key, email.lower().encode(), hashlib.sha256).hexdigest()[:16]
