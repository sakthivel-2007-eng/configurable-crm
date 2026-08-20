"""Variable resolution and quarantine.

CLAUDE.md names this as a known trap: the legacy system stored
`{{site_source_name}}` as a literal option value and corrupted its own taxonomy.
The invariant under test is simple and absolute — **a raw template is never
persisted as an ordinary field value.**
"""

from __future__ import annotations

from app.fields.variables import contains_template, resolve

VARIABLE_FIELDS = frozenset({"source"})


def test_a_resolvable_template_is_substituted() -> None:
    result = resolve(
        {"source": "{{campaign}}"},
        context={"campaign": "spring"},
        variable_fields=VARIABLE_FIELDS,
    )
    assert result.values == {"source": "spring"}
    assert not result.has_quarantine


def test_an_unresolved_template_is_quarantined_not_stored() -> None:
    """The whole point. The value is held back, not written."""
    result = resolve({"source": "{{missing}}"}, context={}, variable_fields=VARIABLE_FIELDS)
    assert "source" not in result.values
    assert result.has_quarantine
    assert result.quarantined[0].unresolved == ("missing",)


def test_a_template_in_a_non_variable_field_is_quarantined_without_substitution() -> None:
    """A field never configured to hold a variable gets no substitution attempt.

    This is the case that rotted the legacy taxonomy: a template arriving in an
    ordinary dropdown became a permanent option whose label was `{{...}}`.
    """
    result = resolve(
        {"stage": "{{campaign}}"},
        context={"campaign": "spring"},
        variable_fields=VARIABLE_FIELDS,
    )
    assert "stage" not in result.values
    assert result.quarantined[0].field_key == "stage"


def test_partial_resolution_is_still_quarantined() -> None:
    """ "Hello {{name}}" with the greeting filled is still a template."""
    result = resolve(
        {"source": "{{known}} and {{unknown}}"},
        context={"known": "yes"},
        variable_fields=VARIABLE_FIELDS,
    )
    assert "source" not in result.values
    assert result.quarantined[0].unresolved == ("unknown",)


def test_ordinary_values_pass_through_untouched() -> None:
    result = resolve(
        {"name": "Ada", "count": 3, "tags": ["a", "b"]},
        context={},
        variable_fields=VARIABLE_FIELDS,
    )
    assert result.values == {"name": "Ada", "count": 3, "tags": ["a", "b"]}
    assert not result.has_quarantine


def test_templates_are_detected_inside_lists_and_objects() -> None:
    """A composite value hides a template just as well as a string does."""
    assert contains_template({"city": "{{town}}"})
    assert contains_template(["safe", "{{unsafe}}"])
    assert not contains_template({"city": "Springfield"})
    assert not contains_template(42)


def test_a_quarantined_value_reports_what_was_raw() -> None:
    """The operator has to be able to fix the source, so the original survives
    in the report even though it never reaches the record."""
    result = resolve({"source": "{{a}}"}, context={}, variable_fields=VARIABLE_FIELDS)
    payload = result.quarantined[0].as_payload()
    assert payload["field_key"] == "source"
    assert payload["raw"] == "{{a}}"
    assert payload["unresolved"] == ["a"]
