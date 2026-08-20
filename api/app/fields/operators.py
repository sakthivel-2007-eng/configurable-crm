"""Filter operators.

Declared here rather than in the filter compiler because the *registry* is what
tells the frontend which operators a field type supports, and M6's compiler must
consume the same list. One definition, two readers.

M2 ships the vocabulary and the per-type mapping. M6 compiles it to SQL.
"""

from __future__ import annotations

import enum

__all__ = ["OPERATOR_ARITY", "Operator"]


class Operator(enum.StrEnum):
    """Comparison vocabulary for the filter DSL."""

    EQUALS = "eq"
    NOT_EQUALS = "ne"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    IN = "in"
    NOT_IN = "not_in"
    GREATER_THAN = "gt"
    GREATER_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_OR_EQUAL = "lte"
    BETWEEN = "between"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"
    # Set membership for TAGS — "has any of", "has all of".
    HAS_ANY = "has_any"
    HAS_ALL = "has_all"
    # Relative date windows, resolved against the workspace timezone at compile
    # time so "last 7 days" means the customer's days, not the server's.
    IN_LAST_DAYS = "in_last_days"
    IN_NEXT_DAYS = "in_next_days"


#: How many operands each operator takes. `0` means the operator is complete on
#: its own (`is_empty`), `2` means it needs a pair (`between`), `-1` means a
#: variable-length list (`in`, `has_any`).
OPERATOR_ARITY: dict[Operator, int] = {
    Operator.EQUALS: 1,
    Operator.NOT_EQUALS: 1,
    Operator.CONTAINS: 1,
    Operator.NOT_CONTAINS: 1,
    Operator.STARTS_WITH: 1,
    Operator.ENDS_WITH: 1,
    Operator.IN: -1,
    Operator.NOT_IN: -1,
    Operator.GREATER_THAN: 1,
    Operator.GREATER_OR_EQUAL: 1,
    Operator.LESS_THAN: 1,
    Operator.LESS_OR_EQUAL: 1,
    Operator.BETWEEN: 2,
    Operator.IS_EMPTY: 0,
    Operator.IS_NOT_EMPTY: 0,
    Operator.HAS_ANY: -1,
    Operator.HAS_ALL: -1,
    Operator.IN_LAST_DAYS: 1,
    Operator.IN_NEXT_DAYS: 1,
}

# Every type supports presence checks; listing them once avoids drift.
PRESENCE: tuple[Operator, ...] = (Operator.IS_EMPTY, Operator.IS_NOT_EMPTY)

TEXTUAL: tuple[Operator, ...] = (
    Operator.EQUALS,
    Operator.NOT_EQUALS,
    Operator.CONTAINS,
    Operator.NOT_CONTAINS,
    Operator.STARTS_WITH,
    Operator.ENDS_WITH,
    Operator.IN,
    Operator.NOT_IN,
    *PRESENCE,
)

NUMERIC: tuple[Operator, ...] = (
    Operator.EQUALS,
    Operator.NOT_EQUALS,
    Operator.GREATER_THAN,
    Operator.GREATER_OR_EQUAL,
    Operator.LESS_THAN,
    Operator.LESS_OR_EQUAL,
    Operator.BETWEEN,
    *PRESENCE,
)

TEMPORAL: tuple[Operator, ...] = (
    Operator.EQUALS,
    Operator.NOT_EQUALS,
    Operator.GREATER_THAN,
    Operator.GREATER_OR_EQUAL,
    Operator.LESS_THAN,
    Operator.LESS_OR_EQUAL,
    Operator.BETWEEN,
    Operator.IN_LAST_DAYS,
    Operator.IN_NEXT_DAYS,
    *PRESENCE,
)

CHOICE: tuple[Operator, ...] = (
    Operator.EQUALS,
    Operator.NOT_EQUALS,
    Operator.IN,
    Operator.NOT_IN,
    *PRESENCE,
)

SET: tuple[Operator, ...] = (
    Operator.HAS_ANY,
    Operator.HAS_ALL,
    Operator.NOT_IN,
    *PRESENCE,
)

BOOLEAN: tuple[Operator, ...] = (Operator.EQUALS, *PRESENCE)
