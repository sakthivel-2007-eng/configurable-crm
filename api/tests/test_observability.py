"""Structured logging, metrics and error reporting (M11).

The point of all three is answering "whose" as well as "what". A stack trace
that says a query was slow is half an answer in a multi-tenant product; the
half that matters operationally is which tenant.

Also asserted: that none of it is load-bearing. Sentry with no DSN, metrics with
nobody scraping, and a logging setup that cannot fail a request — an
observability dependency able to take the product down has inverted its own
purpose.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.factories import build_workspace, login

from app.auth.passwords import PasswordHasherService
from app.config import Settings
from app.main import create_app
from app.observability import METRICS, bind_workspace, configure_sentry, current_request_id

pytestmark = pytest.mark.integration


@pytest.fixture
def hasher(wired_app: FastAPI) -> PasswordHasherService:
    hasher = wired_app.state.password_hasher
    assert isinstance(hasher, PasswordHasherService)
    return hasher


# --- request correlation -----------------------------------------------------


async def test_every_response_carries_a_request_id(api: AsyncClient) -> None:
    """The handle a support report is traced by."""
    # Any route will do — this one needs no fixtures and no lifespan.
    response = await api.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-one"}
    )
    assert response.headers.get("x-request-id"), "no correlation id on the response"


async def test_an_inbound_request_id_is_honoured(api: AsyncClient) -> None:
    """So a trace survives a proxy that already assigned one."""
    response = await api.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-one"},
        headers={"X-Request-Id": "from-the-edge-123"},
    )
    assert response.headers["x-request-id"] == "from-the-edge-123"


async def test_a_hostile_request_id_is_not_echoed_verbatim(api: AsyncClient) -> None:
    """The header is attacker-supplied and ends up in logs.

    A megabyte of control characters in a log aggregator is a denial-of-service
    against the thing you reach for when something is wrong.
    """
    response = await api.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-one"},
        headers={"X-Request-Id": "x" * 5_000},
    )
    assert len(response.headers["x-request-id"]) <= 64


async def test_two_requests_get_different_ids(api: AsyncClient) -> None:
    body = {"email": "nobody@example.com", "password": "wrong-one"}
    first = await api.post("/api/v1/auth/login", json=body)
    second = await api.post("/api/v1/auth/login", json=body)
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


def test_the_request_id_is_empty_outside_a_request() -> None:
    """A context variable, so nothing leaks between requests."""
    assert current_request_id() is None


def test_binding_a_workspace_is_reversible() -> None:
    bind_workspace("11111111-1111-1111-1111-111111111111")
    bind_workspace(None)


# --- metrics -----------------------------------------------------------------


async def test_request_duration_is_recorded_by_route_not_path(
    api: AsyncClient, db_session: AsyncSession, hasher: PasswordHasherService
) -> None:
    """Labelling by path would mint a time series per lead id.

    That is how a metrics store gets melted by a product with uuids in its
    URLs, and it is irreversible once the cardinality is out there.
    """
    fixture = await build_workspace(
        db_session, hasher, name="Metrics Co", owner_email="metrics@example.com"
    )
    await login(api, fixture.owner)
    await api.get(fixture.path("/members"), headers=fixture.owner.auth)

    samples = [
        sample
        for metric in METRICS.registry.collect()
        if metric.name == "crm_request_duration_seconds"
        for sample in metric.samples
    ]
    routes = {sample.labels.get("route") for sample in samples}
    assert any("{workspace_id}" in (route or "") for route in routes), (
        f"routes were labelled by path, not template: {routes}"
    )
    assert not any(str(fixture.workspace.id) in (route or "") for route in routes)


def test_the_five_named_metrics_exist() -> None:
    """The ones `00-milestones.md` names.

    Four measure the asynchronous machinery M8 and M10 built — the parts nobody
    can see by looking at the app, which is exactly why they are worth
    exporting.
    """
    names = {metric.name for metric in METRICS.registry.collect()}
    assert {
        "crm_request_duration_seconds",
        "crm_outbox_depth",
        "crm_intake_requests",
        "crm_index_build_queue",
        "crm_scheduler_lag_seconds",
    } <= names, names


def test_metrics_are_off_unless_a_deployment_turns_them_on() -> None:
    """`/metrics` is unauthenticated, so it must be opt-in.

    Metrics leak shape — request rates, tenant counts, queue depths — and a
    default-on unauthenticated endpoint is one misconfigured ingress away from
    being public.
    """
    app = create_app()
    assert "/metrics" not in app.openapi()["paths"]


def test_metrics_are_served_when_enabled(settings: Settings) -> None:
    enabled = create_app(settings.model_copy(update={"metrics_enabled": True}))
    assert any(getattr(route, "path", None) == "/metrics" for route in enabled.routes), (
        "enabling metrics did not expose the endpoint"
    )


def test_the_registry_is_not_the_process_global_one() -> None:
    """Otherwise a second `create_app()` raises on duplicate collectors.

    The default registry is module state; a test suite that builds the app more
    than once would fail on the second build, which is a confusing way to learn
    about a metrics detail.
    """
    from prometheus_client import REGISTRY

    assert METRICS.registry is not REGISTRY


# --- error reporting ---------------------------------------------------------


def test_sentry_stays_off_without_a_dsn(settings: Settings) -> None:
    """A developer without a DSN should see nothing, and CI should not need one."""
    assert configure_sentry(settings.model_copy(update={"sentry_dsn": None})) is False


def test_the_app_starts_with_no_observability_configured(settings: Settings) -> None:
    """None of it may be load-bearing."""
    app = create_app(settings.model_copy(update={"sentry_dsn": None, "metrics_enabled": False}))
    assert app.state.sentry_enabled is False
