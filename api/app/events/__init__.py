"""The outbound event bus and the inbound intake path (M10).

Three modules, one direction each and one shared rule:

- `envelope` — the wire format, the event names, and the HMAC signature
- `outbox`   — publishing, *inside the caller's transaction*
- `dispatcher` — delivery, retry, and the DEAD threshold, in the worker

The shared rule is architecture rule 8: **nothing here calls an external service
from a request handler.** Publishing writes a row; a worker does the talking.
That is not a performance choice — it is what makes "the lead moved" and "the
event exists" the same fact rather than two that can disagree.
"""

from __future__ import annotations

from app.events.envelope import (
    EVENT_NAMES,
    build_envelope,
    sign,
)
from app.events.outbox import publish

__all__ = ["EVENT_NAMES", "build_envelope", "publish", "sign"]
