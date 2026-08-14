"""Ограничение частоты запросов: token bucket.

Почему token bucket, а не «N запросов в фиксированное окно»: фиксированное
окно допускает всплеск 2N на границе окна (N в конце первого и N в начале
второго). Bucket сглаживает всплески естественным образом и при этом
позволяет короткий бурст на размер корзины - именно то поведение, которого
ждёт живой пользователь.

Два бэкенда:

* Redis - общий счётчик на все воркеры и все узлы. Логика выполняется
  Lua-скриптом: чтение, пополнение и списание происходят атомарно, без
  гонок между процессами.
* Память процесса - запасной вариант. Включается, если Redis не задан
  или упал. Лимит становится «на воркер», то есть фактически в N раз мягче,
  зато сервис продолжает работать. Отказ Redis не должен ронять вход в
  систему - это классический способ превратить сбой кэша в полный простой.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.core.logging import get_logger

log = get_logger("ratelimit")

# Атомарный token bucket. KEYS[1] - ключ корзины.
# ARGV: 1 - ёмкость, 2 - скорость пополнения (токенов/сек), 3 - сейчас (сек),
#       4 - сколько токенов запрошено.
# Возвращает: {разрешено (0/1), остаток токенов, сек до пополнения}.
_BUCKET_LUA = """
local capacity   = tonumber(ARGV[1])
local rate       = tonumber(ARGV[2])
local now        = tonumber(ARGV[3])
local requested  = tonumber(ARGV[4])

local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts     = tonumber(bucket[2])

if tokens == nil then
  tokens = capacity
  ts = now
end

-- пополняем пропорционально прошедшему времени, но не выше ёмкости
local delta = math.max(0, now - ts)
tokens = math.min(capacity, tokens + delta * rate)

local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end

-- TTL с запасом: ключ живёт ровно столько, сколько нужно на полное пополнение
local ttl = math.ceil(capacity / rate) + 60
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], ttl)

local retry = 0
if allowed == 0 then
  retry = math.ceil((requested - tokens) / rate)
end

return {allowed, math.floor(tokens), retry}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: int
    """Секунд до момента, когда запрос пройдёт. 0, если запрос разрешён."""


class _InMemoryBuckets:
    """Fallback-хранилище. Ограничено по размеру, чтобы не стать утечкой памяти."""

    __slots__ = ("_data", "_lock", "_max_keys")

    def __init__(self, max_keys: int = 50_000) -> None:
        self._data: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()
        self._max_keys = max_keys

    async def consume(self, key: str, capacity: float, rate: float, cost: float) -> RateLimitResult:
        now = time.monotonic()
        async with self._lock:
            if len(self._data) >= self._max_keys:
                self._evict(now, rate)
            tokens, ts = self._data.get(key, (capacity, now))
            tokens = min(capacity, tokens + max(0.0, now - ts) * rate)
            if tokens >= cost:
                tokens -= cost
                self._data[key] = (tokens, now)
                return RateLimitResult(True, int(tokens), 0)
            self._data[key] = (tokens, now)
            retry = int((cost - tokens) / rate) + 1
            return RateLimitResult(False, 0, retry)

    def _evict(self, now: float, rate: float) -> None:
        """Выбрасывает корзины, которые всё равно уже пополнились до максимума."""
        horizon = now - (1.0 / max(rate, 1e-6)) * 2
        stale = [k for k, (_, ts) in self._data.items() if ts < horizon]
        for k in stale:
            del self._data[k]
        if not stale:  # все свежие - рубим четверть словаря, иначе застрянем
            for k in list(self._data)[: self._max_keys // 4]:
                del self._data[k]


class RateLimiter:
    """Фасад: сам решает, идти в Redis или считать в памяти."""

    def __init__(self, redis_client: object | None = None, *, enabled: bool = True) -> None:
        self._redis = redis_client
        self._enabled = enabled
        self._memory = _InMemoryBuckets()
        self._script_sha: str | None = None
        self._degraded = False

    def attach_redis(self, client: object | None) -> None:
        """Подключить Redis к уже созданному лимитеру.

        Нужно потому, что объект лимитера создаётся при сборке приложения
        (и сразу попадает в middleware), а соединение с Redis появляется
        позже, в lifespan. Подменять сам объект нельзя - на него уже держит
        ссылку middleware глобального лимита; поэтому Redis «доливается» в
        существующий экземпляр.
        """
        self._redis = client
        self._script_sha = None
        self._degraded = False

    @property
    def degraded(self) -> bool:
        """True, если работаем без Redis (лимиты считаются на каждый воркер)."""
        return self._degraded or self._redis is None

    async def consume(
        self,
        key: str,
        *,
        limit: int,
        window_s: int,
        cost: int = 1,
        burst: int | None = None,
    ) -> RateLimitResult:
        """Списать ``cost`` токенов из корзины ``key``.

        ``limit`` за ``window_s`` задаёт скорость пополнения; ``burst``
        (по умолчанию = limit) - ёмкость корзины.
        """
        if not self._enabled:
            return RateLimitResult(True, limit, 0)

        capacity = float(burst if burst is not None else limit)
        rate = limit / float(window_s)

        if self._redis is not None:
            try:
                return await self._consume_redis(key, capacity, rate, cost)
            except Exception as exc:
                if not self._degraded:
                    log.warning(
                        "ratelimit_redis_unavailable",
                        error=str(exc),
                        action="переключаюсь на подсчёт в памяти процесса",
                    )
                self._degraded = True

        return await self._memory.consume(f"{key}", capacity, rate, float(cost))

    async def _consume_redis(
        self, key: str, capacity: float, rate: float, cost: int
    ) -> RateLimitResult:
        redis = self._redis
        assert redis is not None
        if self._script_sha is None:
            self._script_sha = await redis.script_load(_BUCKET_LUA)  # type: ignore[attr-defined]

        args = [capacity, rate, time.time(), cost]
        try:
            raw = await redis.evalsha(self._script_sha, 1, f"rl:{key}", *args)  # type: ignore[attr-defined]
        except Exception as exc:  # NOSCRIPT после рестарта Redis - перезагружаем скрипт
            if "NOSCRIPT" not in str(exc).upper():
                raise
            self._script_sha = await redis.script_load(_BUCKET_LUA)  # type: ignore[attr-defined]
            raw = await redis.evalsha(self._script_sha, 1, f"rl:{key}", *args)  # type: ignore[attr-defined]

        if self._degraded:
            log.info("ratelimit_redis_recovered")
            self._degraded = False
        return RateLimitResult(bool(raw[0]), int(raw[1]), int(raw[2]))

    async def reset(self, key: str) -> None:
        """Сбросить корзину (например, после успешного входа)."""
        if self._redis is not None:
            try:
                await self._redis.delete(f"rl:{key}")  # type: ignore[attr-defined]
            except Exception:  # noqa: S110 - сброс лимита не критичен
                pass
        self._memory._data.pop(key, None)
