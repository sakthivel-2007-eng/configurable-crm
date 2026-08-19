"""Shared request/response pieces.

`Page` is the envelope every list endpoint returns (architecture rule 9: all
list endpoints are server-paginated). `PageParams` is the matching dependency,
so the 20/100 defaults live in exactly one place.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class Page[ItemT](BaseModel):
    """`{items, total, limit, offset}` — the contract's pagination envelope."""

    model_config = ConfigDict(frozen=True)

    items: list[ItemT]
    total: int
    limit: int
    offset: int


class PageParams(BaseModel):
    """Query parameters for a paginated read."""

    model_config = ConfigDict(frozen=True)

    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0


def page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)
