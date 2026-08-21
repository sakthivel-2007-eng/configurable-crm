"""The inbound intake API (M10).

Unscoped by path — `/api/v1/intake/*` — because the workspace comes from the API
key, not the URL. An integration should not have to be told its own workspace id,
and a URL that carried one would invite somebody to change it.

**Rate limited per key, not per IP.** A busy customer behind one NAT and a
runaway script are different problems; limiting by IP would punish the first for
the second.

Everything here logs, including rejections. A request that produced nothing is
precisely the one somebody will ask about.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.api_keys import ApiKeyScope, require_api_key
from app.errors import api_error
from app.events.intake import MAX_BATCH, IntakeResult, IntakeService
from app.models import IntakeOutcome
from app.schemas.integration import (
    IntakeBatchRequest,
    IntakeBatchResult,
    IntakeLeadRequest,
    IntakeResponse,
)

router = APIRouter(prefix="/intake", tags=["intake"])

#: §Intake: 100 req/min per key.
RATE_LIMIT_PER_MINUTE = 100


def _reason(exc: HTTPException) -> str:
    """The human half of `api_error`'s `{code, message}` detail, or the detail."""
    detail: object = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)[:1000]
    return str(detail)[:1000]


async def _rate_limit(request: Request, scope: ApiKeyScope) -> None:
    """100 requests a minute per key.

    Redis-backed where it is available and a no-op where it is not, because an
    unavailable rate limiter must not become an outage on the *lead intake*
    path. Losing a limit for a minute is cheaper than losing leads.
    """
    redis = getattr(request.app.state, "redis", None)
    if redis is None:  # pragma: no cover - wired in create_app
        return
    key = f"intake:{scope.api_key.id}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
    except Exception:
        return
    if count > RATE_LIMIT_PER_MINUTE:
        raise api_error(
            429,
            "rate_limited",
            f"At most {RATE_LIMIT_PER_MINUTE} intake requests a minute per key",
        )


async def _run(
    scope: ApiKeyScope,
    service: IntakeService,
    body: dict[str, object],
    *,
    endpoint: str,
) -> IntakeResult:
    """Ingest one lead, logging whatever happens.

    An `HTTPException` here is a *data* rejection — an unknown stage, a bad
    dedupe mode — and the log is the only record the sender will ever be able to
    ask about, so it is written before the error propagates.
    """
    try:
        result = await service.ingest_lead(dict(body))
    except HTTPException as exc:
        result = IntakeResult(
            outcome=IntakeOutcome.REJECTED,
            error=_reason(exc),
            status_code=exc.status_code,
        )
        await service.log(endpoint=endpoint, body=dict(body), result=result)
        await scope.session.commit()
        raise
    await service.log(endpoint=endpoint, body=dict(body), result=result)
    return result


@router.post(
    "/leads",
    response_model=IntakeResponse,
    summary="Create or update a lead from an integration",
)
async def intake_lead(
    body: IntakeLeadRequest,
    request: Request,
    scope: Annotated[ApiKeyScope, Depends(require_api_key)],
) -> IntakeResponse:
    """Unknown fields are stored and reported, never rejected."""
    await _rate_limit(request, scope)
    service = IntakeService(scope)
    result = await _run(scope, service, body.model_dump(), endpoint="leads")
    await scope.session.commit()
    return IntakeResponse(outcome=result.outcome, lead_id=result.lead_id, warnings=result.warnings)


@router.post(
    "/leads/batch",
    response_model=IntakeBatchResult,
    summary="Create or update up to 500 leads",
)
async def intake_batch(
    body: IntakeBatchRequest,
    request: Request,
    scope: Annotated[ApiKeyScope, Depends(require_api_key)],
) -> IntakeBatchResult:
    """One bad row does not sink the batch.

    A rejected row is recorded and the rest continue, because the alternative —
    failing the whole request — means a single malformed record in a nightly
    export loses the other 499.
    """
    await _rate_limit(request, scope)
    if len(body.leads) > MAX_BATCH:  # pragma: no cover - pydantic bounds it first
        raise api_error(422, "batch_too_large", f"At most {MAX_BATCH} leads")

    service = IntakeService(scope)
    results: list[IntakeResponse] = []
    counts = {"created": 0, "updated": 0, "skipped": 0, "rejected": 0}

    for entry in body.leads:
        payload = entry.model_dump()
        try:
            outcome = await service.ingest_lead(payload)
        except HTTPException as exc:
            outcome = IntakeResult(
                outcome=IntakeOutcome.REJECTED,
                error=_reason(exc),
                status_code=exc.status_code,
            )
        await service.log(endpoint="leads/batch", body=payload, result=outcome)
        results.append(
            IntakeResponse(
                outcome=outcome.outcome,
                lead_id=outcome.lead_id,
                warnings=outcome.warnings,
            )
        )
        counts[outcome.outcome.value.lower()] = counts.get(outcome.outcome.value.lower(), 0) + 1

    await scope.session.commit()
    return IntakeBatchResult(
        results=results,
        created=counts.get("created", 0),
        updated=counts.get("updated", 0),
        skipped=counts.get("skipped", 0),
        rejected=counts.get("rejected", 0),
    )


@router.get("/ping", status_code=status.HTTP_200_OK, summary="Verify a key")
async def intake_ping(
    scope: Annotated[ApiKeyScope, Depends(require_api_key)],
) -> dict[str, str]:
    """Lets an integrator confirm a key works without creating anything.

    Without it the only way to test a key is to post a real lead, which is how
    test data ends up in a customer's pipeline.
    """
    return {
        "workspace": scope.workspace.name,
        "workspace_id": str(scope.workspace_id),
        "template": scope.template.name,
    }
