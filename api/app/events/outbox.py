"""Publishing to the outbox (M10).

One function, and its contract is the whole point: **it writes rows and does not
flush or commit.** The caller's transaction is what makes the event and the
change it describes the same fact. Committing here — or worse, calling the
endpoint here — would open a window in which the lead had moved and the event had
not, or vice versa, and that window is exactly what a transactional outbox
exists to close.

Fan-out happens at publish time, one row per interested endpoint, because
subscriptions can change between now and delivery. A single row plus a lookup at
delivery would silently send to an endpoint that subscribed after the fact, and
silently miss one that unsubscribed — neither of which the operator asked for.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select

from app.events.envelope import EVENT_NAMES
from app.models import OutboxEvent, WebhookEndpoint
from app.tenancy.session import ScopedSession

__all__ = ["publish"]


async def publish(
    session: ScopedSession,
    *,
    event: str,
    data: dict[str, Any],
    occurred_at: dt.datetime | None = None,
) -> list[OutboxEvent]:
    """Queue `event` for every endpoint subscribed to it.

    Returns the queued rows (usually ignored). Writes nothing when no endpoint
    wants the event, which is the common case in a workspace with no
    integrations — a workspace should not pay for a bus it does not use.

    `data` is stored **unprojected**. Projection happens at delivery against the
    endpoint's own template, so a row queued before a permission was revoked
    still respects the revocation.
    """
    if event not in EVENT_NAMES:  # pragma: no cover - callers pass constants
        raise ValueError(f"{event!r} is not a known event")

    rows = await session.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.workspace_id == session.workspace_id,
            WebhookEndpoint.is_active.is_(True),
        )
    )
    endpoints = [
        endpoint
        for endpoint in rows.scalars().all()
        # An empty subscription list means "everything" — the useful default for
        # somebody wiring up their first integration.
        if not endpoint.events or event in endpoint.events
    ]
    if not endpoints:
        return []

    when = occurred_at or dt.datetime.now(dt.UTC)
    queued: list[OutboxEvent] = []
    for endpoint in endpoints:
        row = OutboxEvent(
            event=event,
            event_id=uuid.uuid4(),
            endpoint_id=endpoint.id,
            payload=data,
            occurred_at=when,
            next_attempt_at=when,
        )
        session.add(row)
        queued.append(row)
    return queued
