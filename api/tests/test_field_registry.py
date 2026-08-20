"""The type registries: validation, normalisation and storage shape.

Pure unit tests — no database. The registry is the one place field *kinds* are
declared, so it earns direct coverage rather than being tested only through the
endpoints that consume it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.fields.registry import (
    ACTION_FIELD_TYPES,
    LEAD_FIELD_TYPES,
    FieldValueError,
    ValidationContext,
    action_type_payloads,
    lead_type_payloads,
)
from app.models.enums import ActionFieldType, LeadFieldType


def ctx(**overrides: object) -> ValidationContext:
    base: dict[str, object] = {
        "default_country_code": "91",
        "currency": "INR",
        "timezone": "Asia/Kolkata",
    }
    base.update(overrides)
    return ValidationContext(**base)  # type: ignore[arg-type]


def normalise(field_type: LeadFieldType, value: object, **overrides: object) -> object:
    return LEAD_FIELD_TYPES[field_type].normalise(value, ctx(**overrides))


# --- the registries themselves ----------------------------------------------


def test_the_lead_registry_has_exactly_the_thirteen_documented_types() -> None:
    """docs/03-configuration-model.md §1.3. The list is closed."""
    assert len(LEAD_FIELD_TYPES) == 13
    assert set(LEAD_FIELD_TYPES) == set(LeadFieldType)


def test_the_action_registry_has_exactly_the_eight_documented_types() -> None:
    """§4.3 — deliberately a different, smaller set."""
    assert len(ACTION_FIELD_TYPES) == 8
    assert set(ACTION_FIELD_TYPES) == set(ActionFieldType)


def test_no_registry_entry_names_a_business_concept() -> None:
    """The registry declares kinds of data, never kinds of business.

    A `COURSE` or `APPLICATION_STATUS` type would be the exact mistake
    CLAUDE.md warns about, and it would be compiled in rather than configured.
    """
    forbidden = {
        "course",
        "product",
        "application",
        "student",
        "admission",
        "enquiry",
        "batch",
        "mql",
        "interview",
    }
    for spec in (*LEAD_FIELD_TYPES.values(), *ACTION_FIELD_TYPES.values()):
        assert not (forbidden & set(spec.key.lower().split("_")))


def test_every_type_declares_a_renderer_and_operators() -> None:
    """The frontend builds inputs from this; an entry without it is unusable."""
    for spec in (*LEAD_FIELD_TYPES.values(), *ACTION_FIELD_TYPES.values()):
        assert spec.renderer.get("widget"), f"{spec.key} has no widget"
        assert spec.operators, f"{spec.key} declares no operators"
        assert spec.storage in {"scalar", "list", "object"}


def test_the_payloads_are_json_serialisable_and_ordered() -> None:
    lead = lead_type_payloads()
    assert [p["key"] for p in lead] == [t.value for t in LeadFieldType]
    assert [p["key"] for p in action_type_payloads()] == [t.value for t in ActionFieldType]
    # Operators serialise as their wire values, not enum reprs.
    assert all(isinstance(op, str) for op in lead[0]["operators"])


# --- scalars -----------------------------------------------------------------


def test_phone_normalises_with_the_workspace_country_code() -> None:
    """Architecture rule 12: never hardcode 91."""
    assert normalise(LeadFieldType.PHONE, "98765 43210") == "+919876543210"
    assert normalise(LeadFieldType.PHONE, "555-0123", default_country_code="1") == "+15550123"
    # A national trunk prefix is dropped when the country code goes on.
    assert normalise(LeadFieldType.PHONE, "09876543210") == "+919876543210"
    # An already-international number is left alone.
    assert normalise(LeadFieldType.PHONE, "+442071234567") == "+442071234567"


def test_phone_rejects_something_that_is_not_a_number() -> None:
    with pytest.raises(FieldValueError):
        normalise(LeadFieldType.PHONE, "call me maybe")


def test_email_is_lowercased() -> None:
    assert normalise(LeadFieldType.EMAIL, "  Person@Example.COM ") == "person@example.com"
    with pytest.raises(FieldValueError):
        normalise(LeadFieldType.EMAIL, "not-an-address")


def test_money_carries_a_currency_and_never_a_float() -> None:
    """Architecture rule 11: numeric(12,2) plus a code, never a float."""
    value = normalise(LeadFieldType.MONEY, "1,234.5")
    assert value == {"amount": "1234.50", "currency": "INR"}
    # The workspace's currency, not a hardcoded INR.
    assert normalise(LeadFieldType.MONEY, 10, currency="USD")["currency"] == "USD"  # type: ignore[index]
    # An explicit currency on the value wins — imports carry mixed currencies.
    assert normalise(LeadFieldType.MONEY, {"amount": "5", "currency": "eur"}) == {
        "amount": "5.00",
        "currency": "EUR",
    }
    # Amounts round rather than truncate, so a half-cent does not vanish.
    assert normalise(LeadFieldType.MONEY, "1.005")["amount"] == "1.01"  # type: ignore[index]


def test_website_gains_a_scheme_rather_than_being_rejected() -> None:
    assert normalise(LeadFieldType.WEBSITE, "acme.com") == "https://acme.com"
    with pytest.raises(FieldValueError):
        normalise(LeadFieldType.WEBSITE, "http://")


def test_checkbox_accepts_the_spellings_a_spreadsheet_produces() -> None:
    for truthy in (True, "true", "YES", "1", 1):
        assert normalise(LeadFieldType.CHECKBOX, truthy) is True
    for falsy in (False, "false", "No", "0", 0):
        assert normalise(LeadFieldType.CHECKBOX, falsy) is False


def test_number_honours_the_configured_bounds() -> None:
    assert normalise(LeadFieldType.NUMBER, "42") == 42
    assert normalise(LeadFieldType.NUMBER, "4.5") == 4.5
    with pytest.raises(FieldValueError):
        normalise(LeadFieldType.NUMBER, 5, config={"min": 10})
    with pytest.raises(FieldValueError):
        # A bool is not a number, even though Python says isinstance(True, int).
        normalise(LeadFieldType.NUMBER, True)


def test_blank_values_all_normalise_to_none() -> None:
    """One representation of absence, so `is_empty` works consistently."""
    for field_type in (
        LeadFieldType.TEXT,
        LeadFieldType.EMAIL,
        LeadFieldType.PHONE,
        LeadFieldType.MONEY,
        LeadFieldType.DATE,
        LeadFieldType.LOCATION,
    ):
        assert normalise(field_type, "") is None
        assert normalise(field_type, None) is None


# --- choice types ------------------------------------------------------------


def test_dropdown_rejects_a_code_that_is_not_an_option() -> None:
    codes = frozenset({"hot", "cold"})
    assert normalise(LeadFieldType.DROPDOWN, "hot", option_codes=codes) == "hot"
    with pytest.raises(FieldValueError):
        normalise(LeadFieldType.DROPDOWN, "lukewarm", option_codes=codes)


def test_tags_dedupe_while_preserving_order() -> None:
    codes = frozenset({"a", "b", "c"})
    assert normalise(LeadFieldType.TAGS, ["b", "a", "b"], option_codes=codes) == ["b", "a"]


# --- the three composites ----------------------------------------------------


def test_dependent_dropdown_stores_both_levels() -> None:
    """Storing the parent too is what keeps 'everyone in this region' from
    needing a recursive join at query time."""
    value = normalise(
        LeadFieldType.DEPENDENT_DROPDOWN,
        "south",
        option_codes=frozenset({"north", "south"}),
        option_parents={"south": "region_a", "north": None},
    )
    assert value == {"parent": "region_a", "value": "south"}


def test_dependent_dropdown_refuses_a_mismatched_pair() -> None:
    """The parent is verified against the tree, not trusted from the client."""
    with pytest.raises(FieldValueError):
        normalise(
            LeadFieldType.DEPENDENT_DROPDOWN,
            {"value": "south", "parent": "region_b"},
            option_codes=frozenset({"south"}),
            option_parents={"south": "region_a"},
        )


def test_recurring_date_derives_the_next_occurrence() -> None:
    """`next` is derived at write time so filtering is a range scan."""
    today = dt.date.today()
    start = today.replace(year=today.year - 3)
    value = normalise(
        LeadFieldType.RECURRING_DATE,
        {"start": start.isoformat(), "frequency": "YEARLY", "interval": 1},
    )
    assert isinstance(value, dict)
    assert value["start"] == start.isoformat()
    following = dt.date.fromisoformat(value["next"])
    assert following >= today
    assert (following.month, following.day) == (start.month, start.day)


def test_recurring_date_accepts_a_bare_date_as_a_yearly_recurrence() -> None:
    """The common shape on import."""
    value = normalise(LeadFieldType.RECURRING_DATE, "2020-03-15")
    assert isinstance(value, dict)
    assert value["start"] == "2020-03-15"
    assert value["frequency"] == "YEARLY"


def test_monthly_recurrence_clamps_to_a_short_month() -> None:
    """31 Jan + 1 month is 28/29 Feb; without the clamp this would raise."""
    value = normalise(
        LeadFieldType.RECURRING_DATE,
        {"start": "2020-01-31", "frequency": "MONTHLY", "interval": 1},
    )
    assert isinstance(value, dict)
    dt.date.fromisoformat(value["next"])  # parses, i.e. a real calendar date


def test_recurring_date_rejects_an_unknown_frequency() -> None:
    with pytest.raises(FieldValueError):
        normalise(
            LeadFieldType.RECURRING_DATE,
            {"start": "2020-01-01", "frequency": "FORTNIGHTLY"},
        )


def test_location_is_structured_not_a_scalar() -> None:
    """§1.3 calls this out: storing 'Chennai, TN' as text makes a region filter
    a substring search."""
    value = normalise(
        LeadFieldType.LOCATION,
        {"city": "Springfield", "state": "IL", "lat": "39.8", "lng": "-89.6"},
    )
    assert value == {"city": "Springfield", "state": "IL", "lat": 39.8, "lng": -89.6}


def test_location_refuses_half_a_coordinate_pair() -> None:
    with pytest.raises(FieldValueError):
        normalise(LeadFieldType.LOCATION, {"city": "X", "lat": 10})


def test_location_rejects_coordinates_off_the_planet() -> None:
    with pytest.raises(FieldValueError):
        normalise(LeadFieldType.LOCATION, {"lat": 99, "lng": 0})


def test_location_keeps_a_single_line_from_an_import() -> None:
    assert normalise(LeadFieldType.LOCATION, "12 High Street") == {"line1": "12 High Street"}


# --- action-only types -------------------------------------------------------


def test_user_type_rejects_a_member_outside_the_workspace() -> None:
    import uuid

    mine, theirs = uuid.uuid4(), uuid.uuid4()
    spec = ACTION_FIELD_TYPES[ActionFieldType.USER]
    assert spec.normalise(str(mine), ctx(membership_ids=frozenset({mine}))) == str(mine)
    with pytest.raises(FieldValueError):
        spec.normalise(str(theirs), ctx(membership_ids=frozenset({mine})))


def test_media_link_refuses_a_private_address() -> None:
    """The URL is fetched by a player in the customer's browser."""
    spec = ACTION_FIELD_TYPES[ActionFieldType.MEDIA_LINK]
    assert spec.normalise("https://cdn.example.com/a.mp3", ctx()) == (
        "https://cdn.example.com/a.mp3"
    )
    with pytest.raises(FieldValueError):
        spec.normalise("http://192.168.1.10/secret.mp3", ctx())
