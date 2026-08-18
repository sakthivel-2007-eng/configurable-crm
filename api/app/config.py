"""Application configuration.

Everything is read from the environment through pydantic-settings. There are no
hardcoded URLs or secrets, and no business configuration here: a workspace's
country code, timezone, currency and taxonomy are database rows, not env vars.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    """Runtime settings, sourced from environment variables or a local `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Runtime -----------------------------------------------------------
    environment: Environment = "local"
    debug: bool = False
    log_level: str = "INFO"

    project_name: str = "Configurable CRM API"
    version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"

    cors_origins: list[str] = Field(default_factory=list)

    # --- Postgres ----------------------------------------------------------
    # Async DSN, e.g. postgresql+asyncpg://user:pass@host:5432/db
    database_url: str
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_pool_timeout_seconds: int = 10

    # --- Redis -------------------------------------------------------------
    redis_url: str

    # --- Object storage (S3-compatible; MinIO locally) ---------------------
    s3_endpoint_url: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_bucket: str
    s3_region: str = "us-east-1"

    # --- Health ------------------------------------------------------------
    health_check_timeout_seconds: float = 5.0

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """The engine is async; a sync DSN would fail confusingly at first query."""
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// driver")
        return value

    @property
    def is_local(self) -> bool:
        return self.environment in ("local", "test")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Call `get_settings.cache_clear()` in tests."""
    # Required values are supplied by the environment, not by call sites.
    return Settings()
