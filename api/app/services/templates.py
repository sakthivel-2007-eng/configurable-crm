"""Message templates and action-value validation (M5).

`docs/01-data-model.md` §5.4. Canned WhatsApp/SMS/email bodies with
`{{field_key}}` substitution, scoped personal, shared or by role.

The security property worth stating plainly: **rendering substitutes from a
lead's values *after* they have passed through `FieldProjectionService`.** A
template naming `{{salary}}` cannot be used to read a field the sender lacks
View on — the placeholder goes unresolved and is reported in the preview. That
is why `render` takes already-projected values rather than a lead.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from app.errors import api_error, not_found
from app.fields.registry import FieldValueError, ValidationContext, action_type_spec
from app.models.enums import TemplateChannel
from app.models.field import ActionField, ActionFieldOption, CustomActionType
from app.models.lead import MessageTemplate
from app.models.workspace import Membership
from app.tenancy.scoping import WorkspaceScope
from app.tenancy.session import ScopedSession

__all__ = ["MessageTemplateService", "validate_action_values"]

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


class MessageTemplateService:
    """CRUD and rendering for message templates."""

    def __init__(self, session: ScopedSession, *, scope: WorkspaceScope) -> None:
        self._session = session
        self._scope = scope

    async def visible(self, *, channel: TemplateChannel | None = None) -> Sequence[MessageTemplate]:
        """Personal, shared, or scoped to the caller's permission template.

        Filtered in SQL rather than in Python: a workspace may hold thousands,
        and "read them all then discard most" is how a list endpoint becomes a
        performance incident.
        """
        statement = self._session.select(MessageTemplate).where(
            MessageTemplate.is_archived.is_(False)
        )
        if channel is not None:
            statement = statement.where(MessageTemplate.channel == channel)

        mine = MessageTemplate.owner_id == self._scope.membership_id
        shared = MessageTemplate.owner_id.is_(None) & MessageTemplate.template_id.is_(None)
        role_scoped = MessageTemplate.template_id == self._scope.template.id

        rows = await self._session.execute(
            statement.where(mine | shared | role_scoped).order_by(MessageTemplate.name)
        )
        templates: Sequence[MessageTemplate] = rows.scalars().all()
        return templates

    async def get(self, template_id: uuid.UUID) -> MessageTemplate:
        template = await self._session.get(MessageTemplate, template_id)
        if template is None or template.is_archived:
            raise not_found("Message template")

        # Visibility is a read rule, so it is enforced on fetch as well as on
        # list — otherwise a personal template's id would be enough to read it.
        visible = {t.id for t in await self.visible()}
        if template.id not in visible:
            raise not_found("Message template")
        return template

    async def create(
        self,
        *,
        channel: TemplateChannel,
        name: str,
        body: str,
        subject: str | None = None,
        shared: bool = False,
        role_template_id: uuid.UUID | None = None,
    ) -> MessageTemplate:
        # Creating something other people will see is an admin act; a personal
        # template is not.
        if (
            (shared or role_template_id is not None)
            and not self._scope.capability("permissions", "edit_templates")
            and not self._scope.is_workspace_admin
        ):
            raise api_error(
                403,
                "insufficient_permissions",
                "Creating a shared or role-scoped template requires template administration rights",
            )

        if channel is TemplateChannel.EMAIL and not subject:
            raise api_error(422, "subject_required", "An email template needs a subject")

        template = MessageTemplate(
            channel=channel,
            name=name.strip(),
            subject=subject,
            body=body,
            owner_id=None if (shared or role_template_id) else self._scope.membership_id,
            template_id=role_template_id,
        )
        self._session.add(template)
        await self._session.flush()
        return template

    async def archive(self, template_id: uuid.UUID) -> MessageTemplate:
        template = await self.get(template_id)
        if template.owner_id != self._scope.membership_id and not self._scope.is_workspace_admin:
            raise api_error(
                403, "not_your_template", "Only the owner or an admin can archive this template"
            )
        template.is_archived = True
        await self._session.flush()
        return template

    def render(self, template: MessageTemplate, *, values: Mapping[str, Any]) -> dict[str, Any]:
        """Substitute `{{field_key}}` from **already-projected** values.

        Unresolved placeholders render empty and are reported, rather than
        being left as literal `{{...}}` in a message a rep is about to send —
        and rather than raising, which would make one missing value block the
        whole compose flow.
        """
        unresolved: list[str] = []

        def substitute(text: str) -> str:
            def replace(match: re.Match[str]) -> str:
                key = match.group(1)
                if key in values and values[key] is not None:
                    return _stringify(values[key])
                unresolved.append(key)
                return ""

            return _PLACEHOLDER.sub(replace, text)

        body = substitute(template.body)
        subject = substitute(template.subject) if template.subject else None

        return {
            "id": str(template.id),
            "channel": template.channel.value,
            "subject": subject,
            "body": body,
            # Deduped and ordered so the preview reads as a checklist.
            "unresolved": sorted(set(unresolved)),
        }

    def payload(self, template: MessageTemplate) -> dict[str, Any]:
        return template.as_payload()


def _stringify(value: Any) -> str:
    """Render a stored value for a message body.

    Composite types get a readable form rather than their JSON: a rep pasting
    `{'amount': '500.00', 'currency': 'INR'}` into WhatsApp is a bug report.
    """
    if isinstance(value, dict):
        if "amount" in value and "currency" in value:
            return f"{value['currency']} {value['amount']}"
        if "value" in value:  # DEPENDENT_DROPDOWN
            return str(value["value"])
        if "start" in value:  # RECURRING_DATE
            return str(value.get("next") or value["start"])
        # LOCATION and anything else composite: the populated text parts.
        parts = [str(v) for k, v in value.items() if k not in ("lat", "lng") and v]
        return ", ".join(parts)
    if isinstance(value, list):
        return ", ".join(_stringify(v) for v in value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


async def validate_action_values(
    scope: WorkspaceScope,
    *,
    action_type: CustomActionType,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a custom action's payload against its own field definitions.

    Reuses M2's action registry rather than duplicating validation — the same
    normalisers, a different type set.
    """
    rows = await scope.session.execute(
        scope.session.select(ActionField).where(ActionField.action_type_id == action_type.id)
    )
    fields = list(rows.scalars().all())

    option_rows = await scope.session.execute(scope.session.select(ActionFieldOption))
    options_by_field: dict[uuid.UUID, list[ActionFieldOption]] = {}
    for option in option_rows.scalars().all():
        options_by_field.setdefault(option.action_field_id, []).append(option)

    member_rows = await scope.session.execute(scope.session.select(Membership))
    membership_ids = frozenset(m.id for m in member_rows.scalars().all())

    errors: dict[str, str] = {}
    out: dict[str, Any] = {}

    for field in fields:
        if field.is_hidden:
            continue
        raw = values.get(field.key)
        spec = action_type_spec(field.field_type)
        context = ValidationContext(
            default_country_code=scope.workspace.default_country_code,
            currency=scope.workspace.currency,
            timezone=scope.workspace.timezone,
            option_codes=(
                frozenset(o.code for o in options_by_field.get(field.id, ()))
                if spec.uses_options
                else None
            ),
            config=field.config or {},
            membership_ids=membership_ids,
        )
        try:
            normalised = spec.normalise(raw, context)
        except FieldValueError as exc:
            errors[field.key] = exc.message
            continue

        if field.is_required and normalised is None:
            errors[field.key] = f"{field.label} is required"
            continue
        out[field.key] = normalised

    if errors:
        raise api_error(
            422, "invalid_action_values", "One or more action values are invalid", fields=errors
        )
    return out
