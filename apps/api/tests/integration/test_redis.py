"""Distributed rate limiter against a real Redis instance."""

from __future__ import annotations

import os

import pytest
from app.net.ratelimit import RateLimiter
from redis.asyncio import Redis

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_redis_bucket_is_shared_and_resettable() -> None:
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")
    redis = Redis.from_url(url, decode_responses=False)
    try:
        await redis.flushdb()
        first = RateLimiter(redis)
        second = RateLimiter(redis)
        assert (await first.consume("integration", limit=1, window_s=60)).allowed
        assert not (await second.consume("integration", limit=1, window_s=60)).allowed
        await second.reset("integration")
        assert (await first.consume("integration", limit=1, window_s=60)).allowed
    finally:
        await redis.aclose()
