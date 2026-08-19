"""Rate limiting for the auth endpoints.

One mechanism throughout: fixed-window counters in Redis, incremented before
the request is verified, refusing with 429 once any counter is over budget.
Counters carry a TTL, so there is nothing to clean up and a restart is not a
bypass.

What differs between endpoints is only *which keys are counted*:

- **Login** counts the submitted email and the client IP. The email stops a slow
  credential-stuffing run against one account; the IP stops a fast one across
  many.
- **Refresh** counts the presented token and the client IP. A refresh token is
  legitimately presented exactly once, so a repeat is a loop or a replay; the IP
  budget is a DoS backstop and is deliberately much larger, because an office
  behind one NAT address shares it.
"""

from __future__ import annotations

import hashlib

from redis.asyncio import Redis

from app.config import Settings

__all__ = ["AuthRateLimiter", "RateLimitExceededError"]


class RateLimitExceededError(Exception):
    """Raised when an auth attempt exceeds its budget.

    Carries the seconds until the window resets so the handler can set
    `Retry-After`.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("auth rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class AuthRateLimiter:
    """Fixed-window counters for the auth endpoints."""

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._login_limit = settings.login_rate_limit_attempts
        self._login_window = settings.login_rate_limit_window_seconds
        self._refresh_token_limit = settings.refresh_rate_limit_attempts
        self._refresh_ip_limit = settings.refresh_rate_limit_ip_attempts
        self._refresh_window = settings.refresh_rate_limit_window_seconds

    # --- login -------------------------------------------------------------

    async def check_login(self, *, email: str, client_ip: str) -> None:
        """Raise `RateLimitExceededError` if either counter is over budget.

        Called before verifying credentials, so a locked-out client cannot use
        the endpoint as a password oracle either.
        """
        await self._check(
            window=self._login_window,
            budgets=[
                (f"login:email:{email.casefold()}", self._login_limit),
                (f"login:ip:{client_ip}", self._login_limit),
            ],
        )

    async def reset_login(self, *, email: str, client_ip: str) -> None:
        """Clear both login counters after a successful sign-in."""
        await self._redis.delete(
            f"login:email:{email.casefold()}",
            f"login:ip:{client_ip}",
        )

    # --- refresh -----------------------------------------------------------

    async def check_refresh(self, *, refresh_token: str, client_ip: str) -> None:
        """Raise `RateLimitExceededError` if either counter is over budget.

        Checked before the token is looked up, so a client that is over budget
        cannot use the endpoint to probe which token values exist.

        There is deliberately no `reset_refresh`. Clearing the IP counter on a
        successful rotation would hand an attacker holding one valid session an
        unlimited budget — they would simply rotate their own token whenever
        they approached the limit. The per-token counter needs no reset either:
        rotation revokes the token, so a well-behaved client never presents the
        same one twice.
        """
        await self._check(
            window=self._refresh_window,
            budgets=[
                (f"refresh:token:{self._fingerprint(refresh_token)}", self._refresh_token_limit),
                (f"refresh:ip:{client_ip}", self._refresh_ip_limit),
            ],
        )

    @staticmethod
    def _fingerprint(token: str) -> str:
        """Hash the token before it becomes a Redis key.

        A raw refresh token in a key is a live credential sitting in a keyspace
        dump or a `MONITOR` trace. Truncated because this only has to be
        collision-resistant enough to separate counters, not to authenticate.
        """
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]

    # --- mechanism ---------------------------------------------------------

    async def _check(self, *, window: int, budgets: list[tuple[str, int]]) -> None:
        """Increment every key, then refuse if any is over its own budget.

        Every counter is incremented before any is judged, so an attempt that
        trips one budget still counts against the others. Returning early would
        let a caller pin one counter at its limit to shield the rest.
        """
        exceeded: int | None = None
        for key, limit in budgets:
            count, ttl = await self._incr_with_window(key, window)
            if count > limit:
                exceeded = max(ttl, 1) if exceeded is None else max(exceeded, max(ttl, 1))

        if exceeded is not None:
            raise RateLimitExceededError(retry_after_seconds=exceeded)

    async def _incr_with_window(self, key: str, window: int) -> tuple[int, int]:
        pipeline = self._redis.pipeline()
        pipeline.incr(key)
        pipeline.ttl(key)
        count, ttl = await pipeline.execute()

        if ttl < 0:
            # First hit in this window (or a key that somehow lost its TTL).
            await self._redis.expire(key, window)
            ttl = window

        return int(count), int(ttl)
