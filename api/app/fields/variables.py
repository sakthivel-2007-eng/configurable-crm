"""`{{variable}}` resolution for `can_use_variable` fields.

The legacy system stored `{{site_source_name}}` and
`{{CUSTOM_ACTION_Lead Source}}` as literal *option values*, which corrupted its
own taxonomy: the dropdown grew a permanent entry whose label was a template.
`docs/03-configuration-model.md` §1.4 calls this out, and CLAUDE.md lists it as
a known trap.

The rule this module enforces: **a raw template is never persisted as an
ordinary field value.** Resolution happens at write time. What cannot be
resolved is quarantined — recorded separately, with the field left empty —
rather than stored as if it were data.

Quarantine rather than rejection is deliberate. An intake payload at 2am with
one unresolved placeholder should not lose the whole lead
(`docs/02-api-contract.md`: "a rejected payload at 2am is a lost lead"). The
operator sees what was quarantined and fixes the source.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from typing import Any

__all__ = ["QuarantinedValue", "ResolutionResult", "contains_template", "resolve"]

#: `{{ token }}` — the token is any run of characters that is not a brace. The
#: legacy data contains spaces and underscores inside tokens, so this must be
#: permissive enough to *recognise* them; recognising is what lets us refuse to
#: store them.
_TEMPLATE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def contains_template(value: Any) -> bool:
    """Whether a value carries an unresolved `{{...}}` anywhere inside it.

    Checked on *every* write, including fields that do not have
    `can_use_variable` set — a template arriving in a field that was never
    meant to accept one is exactly how the legacy taxonomy rotted.
    """
    if isinstance(value, str):
        return bool(_TEMPLATE_RE.search(value))
    if isinstance(value, list):
        return any(contains_template(item) for item in value)
    if isinstance(value, dict):
        return any(contains_template(item) for item in value.values())
    return False


@dataclasses.dataclass(frozen=True, slots=True)
class QuarantinedValue:
    """A value that could not be fully resolved, held out of the record."""

    field_key: str
    raw: Any
    unresolved: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "field_key": self.field_key,
            "raw": self.raw,
            "unresolved": list(self.unresolved),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Outcome of resolving one write's worth of values."""

    values: dict[str, Any]
    quarantined: tuple[QuarantinedValue, ...]

    @property
    def has_quarantine(self) -> bool:
        return bool(self.quarantined)


def _substitute(text: str, context: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Replace every `{{token}}` we can; report the ones we cannot."""
    unresolved: list[str] = []

    def replace(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        if token in context and context[token] is not None:
            return str(context[token])
        unresolved.append(token)
        return match.group(0)

    return _TEMPLATE_RE.sub(replace, text), unresolved


def _resolve_value(value: Any, context: Mapping[str, Any]) -> tuple[Any, list[str]]:
    if isinstance(value, str):
        return _substitute(value, context)
    if isinstance(value, list):
        out_list: list[Any] = []
        misses: list[str] = []
        for item in value:
            resolved, item_misses = _resolve_value(item, context)
            out_list.append(resolved)
            misses.extend(item_misses)
        return out_list, misses
    if isinstance(value, dict):
        out_dict: dict[str, Any] = {}
        misses = []
        for key, item in value.items():
            resolved, item_misses = _resolve_value(item, context)
            out_dict[key] = resolved
            misses.extend(item_misses)
        return out_dict, misses
    return value, []


def resolve(
    values: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    variable_fields: frozenset[str],
) -> ResolutionResult:
    """Resolve templates, quarantining what stays unresolved.

    `variable_fields` is the set of field keys whose definition sets
    `can_use_variable`. A template in any *other* field is quarantined without
    even attempting substitution: the field was never configured to hold one,
    so a value that looks like a template is bad input, not a variable.

    Returns the values safe to persist plus everything held back. Callers
    surface the quarantine — the intake log in M10, the import report in M7,
    a 422 detail for an interactive write.
    """
    resolved_values: dict[str, Any] = {}
    quarantined: list[QuarantinedValue] = []

    for key, value in values.items():
        if not contains_template(value):
            resolved_values[key] = value
            continue

        if key not in variable_fields:
            # Not a variable field: never attempt substitution, never store.
            quarantined.append(
                QuarantinedValue(
                    field_key=key,
                    raw=value,
                    unresolved=tuple(m.strip() for m in _TEMPLATE_RE.findall(str(value))),
                )
            )
            continue

        candidate, unresolved = _resolve_value(value, context)
        if unresolved:
            # Partial resolution is still a template. Storing "Hello {{name}}"
            # with the greeting filled but the name not is exactly the rot we
            # are preventing.
            quarantined.append(
                QuarantinedValue(field_key=key, raw=value, unresolved=tuple(unresolved))
            )
            continue

        resolved_values[key] = candidate

    return ResolutionResult(values=resolved_values, quarantined=tuple(quarantined))
