"""The fictional workspace's own vocabulary.

Every business-shaped string in the product lives in this one file, and it all
belongs to **Northwind Tutors**, a company that does not exist. Keeping it
together rather than scattered through the seeder is deliberate: it makes the
boundary auditable. A grep for any of these names should find them here, in the
seeder that reads them, and nowhere else — certainly not in `app/services`,
`app/fields`, or a migration.

If you are tempted to move any of it closer to the engine, that is the mistake
CLAUDE.md warns about, in its most persuasive form.
"""

from __future__ import annotations

from app.models.enums import ActionDirection, ActionFieldType, LeadFieldType

__all__ = [
    "CUSTOM_ACTIONS",
    "LEAD_FIELDS",
    "LOST_REASONS",
    "MEMBERS",
    "STAGES",
    "TEMPLATE_GRANTS",
    "WORKSPACE_NAME",
]

WORKSPACE_NAME = "Northwind Tutors"

#: One field of every one of the 13 types, including the three composites the
#: known-traps list singles out. `(label, type, options)`.
LEAD_FIELDS: tuple[tuple[str, LeadFieldType, tuple[str, ...]], ...] = (
    ("Guardian Name", LeadFieldType.TEXT, ()),
    ("Enquiry Source", LeadFieldType.DROPDOWN, ("Walk In", "Referral", "Web Form", "Hoarding")),
    ("Subjects", LeadFieldType.TAGS, ("Maths", "Physics", "Chemistry", "Biology", "English")),
    ("Guardian Email", LeadFieldType.EMAIL, ()),
    ("Alternate Contact", LeadFieldType.PHONE, ()),
    ("Transport Needed", LeadFieldType.CHECKBOX, ()),
    ("Enrolment Date", LeadFieldType.DATE, ()),
    ("Quoted Fee", LeadFieldType.MONEY, ()),
    ("Class", LeadFieldType.NUMBER, ()),
    ("Referrer Site", LeadFieldType.WEBSITE, ()),
    # The cascade: a branch, then the batch within it.
    (
        "Branch And Batch",
        LeadFieldType.DEPENDENT_DROPDOWN,
        ("Adyar", "Velachery", "Adyar Morning", "Adyar Evening", "Velachery Morning"),
    ),
    ("Fee Due", LeadFieldType.RECURRING_DATE, ()),
    ("Home Area", LeadFieldType.LOCATION, ()),
)

#: `child -> parent` for the DEPENDENT_DROPDOWN above, by option label.
BRANCH_BATCH_PARENTS: dict[str, str] = {
    "Adyar Morning": "Adyar",
    "Adyar Evening": "Adyar",
    "Velachery Morning": "Velachery",
}

#: Active stages this fixture *adds*. Provisioning already gives every workspace
#: New / Contacted / Won / Lost, so "Contacted" is deliberately absent — adding
#: it again produced two identically-named active stages and a funnel with a
#: step that went nowhere.
STAGES: tuple[str, ...] = ("Demo Booked", "Demo Done", "Negotiating")

LOST_REASONS: tuple[str, ...] = (
    "Fees too high",
    "Chose another centre",
    "Too far away",
    "Not the right year",
)

#: `(name, code, direction, score, fields)` — varied scores and directions so
#: M9's scoring reports have something with a shape.
CUSTOM_ACTIONS: tuple[
    tuple[str, ActionDirection, int, tuple[tuple[str, ActionFieldType], ...]], ...
] = (
    (
        "Demo Class Attended",
        ActionDirection.INBOUND,
        120,
        (("Tutor", ActionFieldType.USER), ("Rating Out Of Five", ActionFieldType.NUMBER)),
    ),
    (
        "Fee Quote Sent",
        ActionDirection.OUTBOUND,
        40,
        (("Amount Quoted", ActionFieldType.NUMBER), ("Valid Until", ActionFieldType.DATE)),
    ),
    (
        "Complaint Raised",
        ActionDirection.INBOUND,
        -150,
        (("Summary", ActionFieldType.TEXT), ("Category", ActionFieldType.DROPDOWN)),
    ),
)

#: `(key, full_name, email, template)`. Five members across the five templates.
MEMBERS: tuple[tuple[str, str, str, str], ...] = (
    ("owner", "Nadia Fernandes", "nadia@northwind-tutors.example", "Root"),
    ("admin", "Ravi Menon", "ravi@northwind-tutors.example", "Admin"),
    ("manager", "Sunita Iyer", "sunita@northwind-tutors.example", "Manager"),
    ("caller_one", "Deepak Rao", "deepak@northwind-tutors.example", "Caller"),
    ("caller_two", "Farah Sheikh", "farah@northwind-tutors.example", "Caller"),
)

#: Field grants per template, as `(template, view, edit, export)` over the
#: *custom* field keys. §8 requires at least one field that is View-but-not-Edit
#: and at least one template whose Export is empty — the observed default is
#: `Export (0) None`, a deliberate exfiltration control.
TEMPLATE_GRANTS: dict[str, dict[str, tuple[str, ...]]] = {
    "Manager": {
        # Sees the fee, cannot change it: the View-but-not-Edit case.
        "view": ("guardian_name", "enquiry_source", "subjects", "quoted_fee", "class", "home_area"),
        "edit": ("guardian_name", "enquiry_source", "subjects", "class"),
        "export": ("guardian_name", "enquiry_source", "class"),
    },
    "Caller": {
        "view": ("guardian_name", "enquiry_source", "subjects", "class", "alternate_contact"),
        "edit": ("guardian_name", "subjects", "class"),
        # Export (0) None — a caller reads a phone number on screen and cannot
        # download ten thousand of them.
        "export": (),
    },
    "Marketing": {
        "view": ("enquiry_source", "referrer_site", "subjects", "home_area"),
        "edit": ("enquiry_source", "referrer_site"),
        "export": ("enquiry_source", "referrer_site"),
    },
}
