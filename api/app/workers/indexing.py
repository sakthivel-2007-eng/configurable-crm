"""The indexed-field worker — the ONLY runtime DDL in the product.

`docs/01-data-model.md` §2.4 and CLAUDE.md architecture rule 7: because customer
values live in JSONB, sorting and filtering on one needs a Postgres *expression
index*. A workspace may declare up to 8 fields as indexed; declaring one
enqueues this worker.

Three constraints this module exists to honour:

1. **`CREATE INDEX CONCURRENTLY`, never `ALTER TABLE`.** Concurrently so a
   50,000-lead workspace does not block writes while the index builds.
2. **Generated safe names.** The index name is derived from ids we control and
   validated against a strict pattern — never interpolated from a customer's
   label. An identifier is not parameterisable in Postgres, so the only safe
   approach is to not let user input near it.
3. **Never from a request handler.** `CREATE INDEX CONCURRENTLY` cannot run
   inside a transaction block, and a request handler is always in one.

The expression indexed is `(workspace_id, (values ->> '<key>'))`, matching the
shape M6's compiler emits for a sort or filter on that field.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.enums import IndexedFieldStatus

__all__ = ["MAX_INDEXED_FIELDS", "build_index", "drop_index", "index_name"]

logger = logging.getLogger(__name__)

#: docs/01-data-model.md §2.4 — "Each workspace may designate up to 8 fields".
MAX_INDEXED_FIELDS = 8

#: Postgres truncates identifiers at 63 bytes. Names are built to fit rather
#: than being silently truncated into a collision.
_MAX_IDENTIFIER = 63

#: What a generated name is allowed to look like. Checked before the name ever
#: reaches a DDL string — belt and braces on top of generating it ourselves.
_SAFE_NAME = re.compile(r"^ix_lv_[0-9a-f]{40}$")

#: Likewise for the JSONB key. Keys are slugified at creation
#: (`app.fields.values.slugify_key`), so this should always pass; if it ever
#: does not, something has written a key we did not generate and the build must
#: refuse rather than interpolate it.
_SAFE_KEY = re.compile(r"^[a-z0-9_]{1,64}$")


class UnsafeIdentifierError(RuntimeError):
    """A generated identifier failed its own safety check.

    Never expected. Raised loudly rather than sanitised, because reaching this
    means an assumption upstream is broken and quietly fixing it would hide
    that.
    """


def index_name(workspace_id: uuid.UUID, field_id: uuid.UUID) -> str:
    """Deterministic, collision-free, injection-proof index name.

    Derived from two uuids and nothing else — in particular, nothing the
    customer typed.

    The two uuids are *hashed* rather than concatenated: spelling both out
    would be 80 characters and Postgres truncates identifiers at 63, so two
    fields in one workspace could silently collide onto the same name. 160 bits
    of SHA-1 over the pair keeps the name 46 characters and unique; the
    `indexed_fields.index_name` column records which field it belongs to, so
    nothing is lost by the name not being human-readable.
    """
    digest = hashlib.sha1(f"{workspace_id.hex}:{field_id.hex}".encode()).hexdigest()
    name = f"ix_lv_{digest}"
    if len(name) > _MAX_IDENTIFIER or not _SAFE_NAME.match(name):
        raise UnsafeIdentifierError(f"generated index name is not safe: {name!r}")
    return name


def _build_statement(name: str, key: str) -> str:
    """The DDL, assembled only from values that passed the safety patterns."""
    if not _SAFE_NAME.match(name):
        raise UnsafeIdentifierError(f"index name failed validation: {name!r}")
    if not _SAFE_KEY.match(key):
        raise UnsafeIdentifierError(f"field key failed validation: {key!r}")
    # The key is single-quoted as a JSONB path literal, and has been validated
    # to contain only [a-z0-9_] — no quote can appear in it to break out.
    return (
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
        f"ON leads (workspace_id, (values ->> '{key}')) "
        f"WHERE deleted_at IS NULL"
    )


async def build_index(
    engine: AsyncEngine,
    *,
    workspace_id: uuid.UUID,
    field_id: uuid.UUID,
    field_key: str,
) -> tuple[IndexedFieldStatus, str | None]:
    """Create the expression index, returning the status to record.

    Runs with `AUTOCOMMIT`: `CREATE INDEX CONCURRENTLY` is rejected inside a
    transaction block, which is precisely why this cannot live in a request
    handler.

    Failure is reported, not raised. A failed index build is a degraded sort,
    not a lost write, and the status column is how the settings UI tells the
    admin what happened.
    """
    name = index_name(workspace_id, field_id)
    statement = _build_statement(name, field_key)

    try:
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(statement))
    except Exception as exc:
        logger.warning(
            "indexed_field.build_failed",
            extra={"workspace_id": str(workspace_id), "field_id": str(field_id)},
            exc_info=exc,
        )
        return IndexedFieldStatus.FAILED, str(exc)[:500]

    logger.info(
        "indexed_field.built",
        extra={"workspace_id": str(workspace_id), "index": name},
    )
    return IndexedFieldStatus.READY, None


async def drop_index(
    engine: AsyncEngine,
    *,
    workspace_id: uuid.UUID,
    field_id: uuid.UUID,
) -> None:
    """Drop the expression index when a workspace un-declares the field.

    `CONCURRENTLY` again, and `IF EXISTS` so un-declaring a field whose build
    failed is not itself an error.
    """
    name = index_name(workspace_id, field_id)
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
