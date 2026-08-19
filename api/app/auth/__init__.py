"""Authentication and identity resolution.

Contents:

- `passwords`  — argon2id hashing and verification
- `tokens`     — JWT access tokens, opaque refresh tokens, rotation
- `rate_limit` — Redis-backed login throttling
- `service`    — login / refresh / logout, and the licence + activity gates
- `deps`       — FastAPI dependencies resolving the caller from a bearer token

Workspace scoping lives in `app.tenancy`, not here. This package answers "who is
this?"; that one answers "what may they touch?".
"""

from __future__ import annotations
