"""The field definition engine (M2).

The single source of truth for what *kinds* of data the product understands.
M3's custom actions, M4's permission matrix, M5's lead values and M6's filter
compiler all read their type information from here — none of them re-declare it.

- `registry`   — the 13 lead types and 8 action types, each declaring
                 validation, normalisation, storage shape, operators and a
                 renderer contract
- `operators`  — the filter vocabulary the registry maps types onto
- `values`     — validating a whole `values` blob against a workspace's fields
- `variables`  — `{{...}}` resolution at write time for `can_use_variable`
"""

from __future__ import annotations

from app.fields.operators import Operator
from app.fields.registry import (
    ACTION_FIELD_TYPES,
    LEAD_FIELD_TYPES,
    FieldTypeSpec,
    FieldValueError,
    ValidationContext,
    action_type_payloads,
    action_type_spec,
    lead_type_payloads,
    lead_type_spec,
)

__all__ = [
    "ACTION_FIELD_TYPES",
    "LEAD_FIELD_TYPES",
    "FieldTypeSpec",
    "FieldValueError",
    "Operator",
    "ValidationContext",
    "action_type_payloads",
    "action_type_spec",
    "lead_type_payloads",
    "lead_type_spec",
]
