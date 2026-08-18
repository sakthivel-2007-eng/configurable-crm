"""Health endpoint contract: status aggregation, status codes, both mounts."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.dependencies import get_health_service
from app.schemas.health import ComponentHealth, HealthChecks, HealthResponse

HEALTHY = ComponentHealth(status="ok", latency_ms=1.0)
BROKEN = ComponentHealth(status="error", latency_ms=5.0, error="ConnectionError: refused")


class StubHealthService:
    """Stands in for HealthService so the endpoint is tested, not the probes."""

    def __init__(self, response: HealthResponse) -> None:
        self._response = response

    async def check(self) -> HealthResponse:
        return self._response


def _response(
    *, database: ComponentHealth, redis: ComponentHealth, storage: ComponentHealth
) -> HealthResponse:
    degraded = any(c.status != "ok" for c in (database, redis, storage))
    return HealthResponse(
        status="degraded" if degraded else "ok",
        service="Configurable CRM API",
        version="0.1.0",
        environment="test",
        checks=HealthChecks(database=database, redis=redis, object_storage=storage),
    )


def _override(app: FastAPI, response: HealthResponse) -> None:
    app.dependency_overrides[get_health_service] = lambda: StubHealthService(response)


@pytest.mark.parametrize("path", ["/health", "/api/v1/health"])
async def test_health_is_green_when_every_check_passes(
    app: FastAPI, client: AsyncClient, path: str
) -> None:
    _override(app, _response(database=HEALTHY, redis=HEALTHY, storage=HEALTHY))

    response = await client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"
    assert body["checks"]["object_storage"]["status"] == "ok"


@pytest.mark.parametrize("failing", ["database", "redis", "object_storage"])
async def test_health_is_503_when_any_dependency_fails(
    app: FastAPI, client: AsyncClient, failing: str
) -> None:
    components = dict.fromkeys(("database", "redis", "storage"), HEALTHY)
    components["storage" if failing == "object_storage" else failing] = BROKEN
    _override(app, _response(**components))

    response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"][failing]["status"] == "error"


async def test_health_reports_service_identity(app: FastAPI, client: AsyncClient) -> None:
    _override(app, _response(database=HEALTHY, redis=HEALTHY, storage=HEALTHY))

    body = (await client.get("/health")).json()

    assert body["service"] == "Configurable CRM API"
    assert body["version"] == "0.1.0"
    assert body["environment"] == "test"


async def test_root_health_is_absent_from_the_openapi_schema(client: AsyncClient) -> None:
    """One health operation in the schema, so the generated client has one method."""
    schema = (await client.get("/openapi.json")).json()

    assert "/api/v1/health" in schema["paths"]
    assert "/health" not in schema["paths"]
