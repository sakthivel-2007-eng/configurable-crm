"""The outbound wire format and its signature (M10).

`docs/02-api-contract.md` §Outbound fixes the envelope and the three headers.
Two details in it are the whole reliability story:

**`X-CRM-Event-Id` is stable across retries.** A consumer that dedupes on it
sees one event however many times we deliver. Regenerating per attempt would
turn one retried delivery into eight distinct events at the far end, which is
exactly the failure an at-least-once bus is supposed to let consumers absorb.

**`X-CRM-Signature` is HMAC-SHA256 over the exact bytes sent.** Over the bytes,
not over the dict — re-serialising on the consumer's side and hashing that would
fail on key order, and the mismatch would look like an attack rather than a bug.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import uuid
from typing import Any

__all__ = ["EVENT_NAMES", "build_envelope", "serialise", "sign", "verify"]

#: Every event the product emits (§Outbound). Product concepts — none of them
#: names anything a customer would recognise as their own vocabulary, which is
#: why this is a constant and stages are not.
EVENT_NAMES: frozenset[str] = frozenset(
    {
        "lead.created",
        "lead.updated",
        "lead.stage_changed",
        "lead.assigned",
        "lead.field_changed",
        "action.created",
        "task.created",
        "task.completed",
    }
)


def build_envelope(
    *,
    event: str,
    event_id: uuid.UUID,
    workspace_id: uuid.UUID,
    occurred_at: dt.datetime,
    data: dict[str, Any],
) -> dict[str, Any]:
    """The documented envelope. `data` is already projected by the caller."""
    return {
        "event": event,
        "event_id": str(event_id),
        "workspace_id": str(workspace_id),
        "occurred_at": occurred_at.astimezone(dt.UTC).isoformat().replace("+00:00", "Z"),
        "data": data,
    }


def serialise(envelope: dict[str, Any]) -> bytes:
    """The exact bytes signed and sent.

    `sort_keys` and a compact separator so the same envelope always produces the
    same bytes — a signature over unstable serialisation is a signature that
    fails intermittently, which is worse than none.
    """
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(secret: str, body: bytes) -> str:
    """`sha256=<hex>`, over the bytes actually sent."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(secret: str, body: bytes, signature: str) -> bool:
    """Constant-time check. Exposed so the `/test` endpoint can prove itself."""
    return hmac.compare_digest(sign(secret, body), signature)
