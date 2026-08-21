"""What goes into `leads.search_vector`, and how it is spelled.

`docs/01-data-model.md` §4 keeps a maintained `tsvector` on every lead so that
free-text search covers a workspace's *own* fields — which cannot be columns,
because the workspace invented them. This module turns a lead's JSONB `values`
into the text that vector is built from.

Two decisions worth stating, because both are easy to get wrong later:

**Which fields.** The type registry decides, via `spec.searchable`. There is no
per-field toggle: §1.4 of the configuration model defines exactly four field
properties and searchable is not one of them, so adding a fifth would be
inventing configuration surface the product does not have. Text, addresses and
option codes are what people type into a search box; amounts, dates and
checkboxes are what they filter on.

**Which dictionary.** `simple`, not `english`. This corpus is overwhelmingly
proper nouns — people, companies, places, option codes — and an English stemmer
mangles those (`Manning` stems to `man`) while its stopword list would drop a
lead whose name is a common word. `simple` lowercases and does nothing else,
which is what name lookup wants.

Partial matching on the identity field is *not* this vector's job: a tsquery
matches whole lexemes, so searching a phone fragment goes through the trigram
index on `identity_value` instead. The two are complementary and the list
endpoint uses both.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.fields.registry import LEAD_FIELD_TYPES
from app.models.field import LeadField

__all__ = ["SEARCH_CONFIG", "search_text_for", "searchable_keys"]

#: The Postgres text-search configuration every vector and query is built with.
#: Both sides must agree, so it is named once here rather than spelled at each
#: call site.
SEARCH_CONFIG = "simple"

#: Location sub-keys worth indexing. `lat`/`lng` are deliberately excluded —
#: nobody searches for a lead by typing its latitude.
_LOCATION_KEYS = ("line1", "line2", "city", "state", "postal_code", "country")


def searchable_keys(fields: Sequence[LeadField]) -> list[str]:
    """The keys of the fields whose values belong in the vector."""
    return [f.key for f in fields if LEAD_FIELD_TYPES[f.field_type].searchable]


def _flatten(value: Any) -> Iterable[str]:
    """Every string worth indexing inside one field's stored value.

    Handles the three storage shapes without asking which one it was given:
    scalars yield themselves, lists yield their members, and objects yield the
    sub-keys that hold text. Anything else — a number, a bool — yields nothing,
    which is what keeps a non-searchable value out even if a caller passes one.
    """
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
            # Option codes are slugs of their labels (`slugify_key`), so
            # "not_interested" also contributes "not interested" — otherwise a
            # search for the words a user actually reads on screen would miss.
            if "_" in text:
                yield text.replace("_", " ")
            # A stored phone is E.164 (`+919000000001`). Yielding the digits
            # alone lets a whole-number search match without the plus.
            if text.startswith("+") and text[1:].isdigit():
                yield text[1:]
    elif isinstance(value, list):
        for item in value:
            yield from _flatten(item)
    elif isinstance(value, dict):
        # DEPENDENT_DROPDOWN stores {value, parent}; LOCATION stores an
        # address. Both are indexed by their text parts only.
        for key in ("value", "parent", *_LOCATION_KEYS):
            if isinstance(entry := value.get(key), str):
                yield from _flatten(entry)


def search_text_for(
    values: Mapping[str, Any],
    fields: Sequence[LeadField],
    *,
    identity_value: str | None = None,
) -> str:
    """The document to build a lead's `search_vector` from.

    `identity_value` is included because it is the one value a user is most
    likely to search by, and it is a denormalised copy that may point at a
    field the workspace later changes.

    Deduplicated, order-preserving: a lead whose email and identity are the
    same string should not weight that string twice.
    """
    seen: dict[str, None] = {}

    if identity_value:
        for part in _flatten(identity_value):
            seen.setdefault(part, None)

    for key in searchable_keys(fields):
        if (value := values.get(key)) is None:
            continue
        for part in _flatten(value):
            seen.setdefault(part, None)

    return "\n".join(seen)
