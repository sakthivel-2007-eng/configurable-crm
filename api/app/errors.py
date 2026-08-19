"""Error helpers.

Every error this API returns has the shape `{"detail": {"code", "message"}}`
(docs/02-api-contract.md). Handlers raise these constructors rather than
assembling dicts, so the codes stay greppable and the shape stays uniform.

One rule deserves calling out: **absent, soft-deleted, and belongs-to-another-
workspace all return 404, never 403.** A 403 confirms the resource exists,
which leaks the shape of another tenant's data. `not_found` is the only
constructor callers should reach for in those cases.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

__all__ = [
    "api_error",
    "conflict",
    "forbidden",
    "not_found",
    "unauthorized",
    "unprocessable",
]


def api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
    **extra: Any,  # per-code payload, e.g. the offending field ids
) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    detail.update(extra)
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def not_found(resource: str = "Resource") -> HTTPException:
    """404 for absent, soft-deleted, or cross-workspace resources alike.

    Do not add a distinguishing code per case — the whole point is that the
    caller cannot tell them apart.
    """
    return api_error(status.HTTP_404_NOT_FOUND, "not_found", f"{resource} not found")


def unauthorized(code: str, message: str) -> HTTPException:
    return api_error(
        status.HTTP_401_UNAUTHORIZED,
        code,
        message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def forbidden(code: str, message: str, **extra: Any) -> HTTPException:
    return api_error(status.HTTP_403_FORBIDDEN, code, message, **extra)


def conflict(code: str, message: str, **extra: Any) -> HTTPException:
    return api_error(status.HTTP_409_CONFLICT, code, message, **extra)


def unprocessable(code: str, message: str, **extra: Any) -> HTTPException:
    # 422 literal rather than `status.HTTP_422_UNPROCESSABLE_ENTITY`: Starlette
    # renamed that constant and deprecated the old spelling, and the API
    # contract pins the number, not the name.
    return api_error(422, code, message, **extra)
