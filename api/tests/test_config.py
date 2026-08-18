"""Settings behaviour."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
        "redis_url": "redis://localhost:6379/0",
        "s3_endpoint_url": "http://localhost:9000",
        "s3_access_key_id": "key",
        "s3_secret_access_key": "secret",
        "s3_bucket": "bucket",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_sync_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        _settings(database_url="postgresql://u:p@localhost:5432/db")


def test_async_database_url_is_accepted() -> None:
    assert _settings().database_url.startswith("postgresql+asyncpg://")


def test_local_and_test_environments_are_local() -> None:
    assert _settings(environment="local").is_local
    assert _settings(environment="test").is_local
    assert not _settings(environment="production").is_local


def test_cors_origins_default_to_empty() -> None:
    """No hardcoded origins: deployment supplies them.

    Read off the field default rather than an instance, since the ambient test
    environment sets CORS_ORIGINS.
    """
    factory = Settings.model_fields["cors_origins"].default_factory
    assert factory is not None
    assert factory() == []  # type: ignore[call-arg]  # zero-arg factory
