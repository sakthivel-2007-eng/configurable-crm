"""Argon2id password hashing.

Parameters come from settings so a bigger production host can raise the memory
cost without a code change. `needs_rehash` lets us transparently upgrade a
stored hash the next time its owner logs in successfully.
"""

from __future__ import annotations

import contextlib

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions
from argon2.low_level import Type

from app.config import Settings

__all__ = ["PasswordHasherService"]


class PasswordHasherService:
    """Thin wrapper over argon2-cffi, pinned to argon2id."""

    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
            type=Type.ID,
        )
        # Computed once, with the live parameters, so the timing it burns on the
        # unknown-email path actually matches a real verification.
        self._dummy_hash = self._hasher.hash("password-that-authenticates-nobody")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """Constant-time verify. Returns False rather than raising."""
        try:
            return self._hasher.verify(password_hash, password)
        except argon2_exceptions.VerificationError:
            return False
        except argon2_exceptions.InvalidHashError:
            # A malformed stored hash is a data problem, not a wrong password,
            # but from the caller's perspective the answer is the same: no.
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        """True when the stored hash predates the current cost parameters."""
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except argon2_exceptions.InvalidHashError:
            return True

    def dummy_verify(self) -> None:
        """Burn a comparable amount of time when the user does not exist.

        Without this, "unknown email" returns measurably faster than "wrong
        password", which enumerates accounts.
        """
        with contextlib.suppress(argon2_exceptions.VerificationError):
            self._hasher.verify(self._dummy_hash, "not-the-password")
