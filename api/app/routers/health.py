"""Health endpoint.

Mounted twice by `app.main`: at `/health` for container and load-balancer
probes, and under the versioned prefix for API clients. One implementation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.dependencies import get_health_service
from app.schemas.health import HealthResponse
from app.services.health import HealthService

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness and dependency health",
    responses={
        status.HTTP_200_OK: {"description": "Every backing service responded."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "At least one backing service failed its probe.",
            "model": HealthResponse,
        },
    },
)
async def get_health(
    response: Response,
    service: HealthService = Depends(get_health_service),
) -> HealthResponse:
    report = await service.check()
    if report.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
