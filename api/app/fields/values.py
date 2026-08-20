"""Validating a whole `values` blob against a workspace's field definitions.

The registry validates *one value against one type*. This module is the layer
above: given the workspace's fields and an incoming `{key: value}` mapping, it
resolves variables, normalises every value, enforces required and
`lock_after_create`, and returns the JSONB blob to persist.

Every write path uses this — the lead endpoints in M5, import in M7, intake in
M10. There is one implementation so the intake API cannot accidentally accept
something the UI rejects.

Note the ordering, which matters:

    variable resolution  ->  normalisation  ->  required/lock checks

Variables first, because a `{{...}}` template must never reach a type validator
that might coerce it into something storable.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.fields.registry import (
    FieldValueError,
    ValidationContext,
    lead_type_spec,
)
from app.fields.variables import QuarantinedValue, resolve
from app.models.enums import LeadFieldType
from app.models.field import FieldOption, LeadField

__all__ = ["FieldValidationError", "ValidatedValues", "ValueValidator"]


class FieldValidationError(Exception):
    """One or more values failed validation.

    Aggregates rather than failing on the first problem: a form with three bad
    fields should report three, not send the user round the loop three times.
    """

    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = dict(errors)


@dataclasses.dataclass(frozen=True, slots=True)
class ValidatedValues:
    """The outcome of validating one write."""

    #: Normalised values, ready for the JSONB column.
    values: dict[str, Any]
    #: Templates held back rather than persisted.
    quarantined: tuple[QuarantinedValue, ...]
    #: Keys present in the payload that match no field definition. Accepted and
    #: reported rather than rejected — `docs/02-api-contract.md` requires the
    #: intake API to keep unknown keys and warn.
    unknown_keys: tuple[str, ...]


class ValueValidator:
    """Validates `values` payloads for one workspace.

    Constructed with the workspace's field definitions and locale, so the
    per-value work is pure. Build it once per request, not once per value.
    """

    def __init__(
        self,
        fields: Sequence[LeadField],
        *,
        default_country_code: str,
        currency: str,
        timezone: str,
        options_by_field: Mapping[uuid.UUID, Sequence[FieldOption]] | None = None,
    ) -> None:
        self._fields = {f.key: f for f in fields}
        self._default_country_code = default_country_code
        self._currency = currency
        self._timezone = timezone
        self._options = options_by_field or {}
        self._variable_fields = frozenset(f.key for f in fields if f.can_use_variable)

    @property
    def field_keys(self) -> frozenset[str]:
        return frozenset(self._fields)

    def _context_for(self, field: LeadField) -> ValidationContext:
        spec = lead_type_spec(field.field_type)
        option_codes: frozenset[str] | None = None
        option_parents: dict[str, str | None] | None = None

        if spec.uses_options:
            options = self._options.get(field.id, ())
            live = [o for o in options if not o.is_archived]
            option_codes = frozenset(o.code for o in live)
            if field.field_type is LeadFieldType.DEPENDENT_DROPDOWN:
                by_id = {o.id: o for o in options}
                option_parents = {
                    o.code: (
                        by_id[o.parent_option_id].code
                        if o.parent_option_id and o.parent_option_id in by_id
                        else None
                    )
                    for o in live
                }

        return ValidationContext(
            default_country_code=self._default_country_code,
            currency=self._currency,
            timezone=self._timezone,
            option_codes=option_codes,
            option_parents=option_parents,
            config=field.config or {},
        )

    def validate(
        self,
        payload: Mapping[str, Any],
        *,
        is_create: bool,
        variable_context: Mapping[str, Any] | None = None,
        existing: Mapping[str, Any] | None = None,
        enforce_required: bool = True,
    ) -> ValidatedValues:
        """Normalise a write payload into the blob to persist.

        `is_create` drives two rules: `lock_after_create` fields are writable
        only on create, and required fields are only enforced when the whole
        record is being written (a PATCH of one field must not fail because a
        different required field is absent from *this* payload).
        """
        resolution = resolve(
            payload,
            context=variable_context or {},
            variable_fields=self._variable_fields,
        )

        errors: dict[str, str] = {}
        unknown: list[str] = []
        out: dict[str, Any] = {}

        for key, raw in resolution.values.items():
            field = self._fields.get(key)
            if field is None:
                # Unknown keys are kept verbatim and reported, never rejected.
                unknown.append(key)
                out[key] = raw
                continue

            if field.is_hidden and raw is not None:
                errors[key] = "This field is hidden and cannot be written"
                continue

            if field.lock_after_create and not is_create:
                errors[key] = "This field is locked after the record is created"
                continue

            spec = lead_type_spec(field.field_type)
            try:
                out[key] = spec.normalise(raw, self._context_for(field))
            except FieldValueError as exc:
                errors[key] = exc.message

        if enforce_required and is_create:
            merged = {**(existing or {}), **out}
            for key, field in self._fields.items():
                if not field.is_required or field.is_hidden:
                    continue
                if merged.get(key) is None:
                    errors.setdefault(key, f"{field.label} is required")

        if errors:
            raise FieldValidationError(errors)

        return ValidatedValues(
            values=out,
            quarantined=resolution.quarantined,
            unknown_keys=tuple(unknown),
        )

    def project_labels(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Decorate stored codes with their option labels and colours.

        The API returns codes (stable) and the UI needs labels (human). Doing it
        here rather than in the frontend means an archived option still renders
        with the label it had, instead of showing a bare code.
        """
        decorated: dict[str, Any] = {}
        for key, value in values.items():
            field = self._fields.get(key)
            if field is None or value is None:
                continue
            spec = lead_type_spec(field.field_type)
            if not spec.uses_options:
                continue
            by_code = {o.code: o for o in self._options.get(field.id, ())}

            if field.field_type is LeadFieldType.TAGS and isinstance(value, list):
                decorated[key] = [_option_payload(by_code.get(c), c) for c in value]
            elif field.field_type is LeadFieldType.DEPENDENT_DROPDOWN and isinstance(value, dict):
                code = value.get("value")
                if code:
                    decorated[key] = _option_payload(by_code.get(code), code)
            elif isinstance(value, str):
                decorated[key] = _option_payload(by_code.get(value), value)
        return decorated


def _option_payload(option: FieldOption | None, code: str) -> dict[str, Any]:
    if option is None:
        # The option was deleted outright at some point in the past. Render the
        # code rather than dropping the value — history is not ours to erase.
        return {"code": code, "label": code, "color": None, "archived": True}
    return {
        "code": option.code,
        "label": option.label,
        "color": option.color,
        "archived": option.is_archived,
    }


def slugify_key(label: str, *, taken: Iterable[str] = ()) -> str:
    """Derive the immutable JSONB key from a field's label.

    Generated once at creation. `docs/01-data-model.md` §3.1: "`key` is
    generated from the label on create and then **immutable**".
    """
    import re
    import unicodedata

    normalised = unicodedata.normalize("NFKD", label)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_only.lower()).strip("_")
    slug = slug or "field"
    slug = slug[:56]

    existing = set(taken)
    if slug not in existing:
        return slug
    for suffix in range(2, 1000):
        candidate = f"{slug}_{suffix}"
        if candidate not in existing:
            return candidate
    return f"{slug}_{uuid.uuid4().hex[:6]}"
