"""Тесты token-bucket лимитера в режиме in-memory (без Redis)."""

from __future__ import annotations

import pytest
from app.net.ratelimit import RateLimiter


@pytest.fixture
def limiter() -> RateLimiter:
    return RateLimiter(redis_client=None, enabled=True)


class TestTokenBucket:
    async def test_allows_within_limit(self, limiter: RateLimiter) -> None:
        for _ in range(5):
            result = await limiter.consume("k", limit=5, window_s=60)
            assert result.allowed

    async def test_blocks_over_limit(self, limiter: RateLimiter) -> None:
        for _ in range(5):
            await limiter.consume("k", limit=5, window_s=60)
        result = await limiter.consume("k", limit=5, window_s=60)
        assert not result.allowed
        assert result.retry_after > 0

    async def test_separate_keys_independent(self, limiter: RateLimiter) -> None:
        for _ in range(5):
            await limiter.consume("a", limit=5, window_s=60)
        # Другой ключ не затронут исчерпанием первого.
        result = await limiter.consume("b", limit=5, window_s=60)
        assert result.allowed

    async def test_burst_capacity(self, limiter: RateLimiter) -> None:
        # burst > limit разрешает короткий всплеск.
        allowed = 0
        for _ in range(10):
            if (await limiter.consume("k", limit=5, window_s=60, burst=10)).allowed:
                allowed += 1
        assert allowed == 10

    async def test_disabled_always_allows(self) -> None:
        limiter = RateLimiter(redis_client=None, enabled=False)
        for _ in range(1000):
            assert (await limiter.consume("k", limit=1, window_s=60)).allowed

    async def test_reset_restores_quota(self, limiter: RateLimiter) -> None:
        for _ in range(5):
            await limiter.consume("k", limit=5, window_s=60)
        assert not (await limiter.consume("k", limit=5, window_s=60)).allowed
        await limiter.reset("k")
        assert (await limiter.consume("k", limit=5, window_s=60)).allowed

    async def test_degraded_flag_without_redis(self, limiter: RateLimiter) -> None:
        # Без Redis лимитер честно сообщает, что работает в режиме «на воркер».
        assert limiter.degraded is True
