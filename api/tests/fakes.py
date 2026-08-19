"""Test doubles.

`FakeRedis` implements exactly the four operations `LoginRateLimiter` uses.
A real Redis container would test redis-py rather than our rate limiting, and
the limiter's contract — fixed-window counting with a TTL — is fully observable
through this.
"""

from __future__ import annotations

import time
from typing import Any, Self


class _FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, str]] = []

    def incr(self, key: str) -> Self:
        self._queued.append(("incr", key))
        return self

    def ttl(self, key: str) -> Self:
        self._queued.append(("ttl", key))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for operation, key in self._queued:
            if operation == "incr":
                results.append(await self._redis.incr(key))
            else:
                results.append(await self._redis.ttl(key))
        self._queued.clear()
        return results


class FakeRedis:
    """In-memory counters with expiry, matching redis-py's async surface."""

    def __init__(self) -> None:
        self._values: dict[str, int] = {}
        self._expires_at: dict[str, float] = {}

    def _sweep(self, key: str) -> None:
        expiry = self._expires_at.get(key)
        if expiry is not None and expiry <= time.monotonic():
            self._values.pop(key, None)
            self._expires_at.pop(key, None)

    async def incr(self, key: str) -> int:
        self._sweep(key)
        self._values[key] = self._values.get(key, 0) + 1
        return self._values[key]

    async def ttl(self, key: str) -> int:
        self._sweep(key)
        if key not in self._values:
            return -2  # redis: key does not exist
        expiry = self._expires_at.get(key)
        if expiry is None:
            return -1  # redis: key exists with no TTL
        return max(int(expiry - time.monotonic()), 0)

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self._values:
            return False
        self._expires_at[key] = time.monotonic() + seconds
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self._values.pop(key, None) is not None:
                removed += 1
            self._expires_at.pop(key, None)
        return removed

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def aclose(self) -> None:
        self._values.clear()
        self._expires_at.clear()
