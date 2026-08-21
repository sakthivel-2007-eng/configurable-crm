"""The filter DSL — what a filter *is*, before any SQL exists.

`docs/01-data-model.md` §6.1. Two families of node, defined together and on
purpose:

- **Field rules** ask about a lead's current state: `stage = X`, `budget > 5000`.
- **History predicates** ask about its timeline: "no outgoing call in 14 days",
  "status went from HOT to Lost last week".

`04-feature-coverage.md` calls history filtering "the worst miss in the audit" —
the original DSL could only see current state, and four of the ten filters people
actually used query history. Shipping field rules first would grow a compiler
that only knows how to emit `WHERE`, and every history predicate afterwards would
be a rewrite. So both node kinds exist from the first commit, and the compiler
returns a boolean expression either way.

Everything here is a Pydantic model with no database knowledge whatsoever. That
separation is what lets `POST /leads/search`, saved filters, and — from M8 —
assignment-rule conditions all speak one language: the DSL is the noun, and
`compiler.py` is the only thing that turns it into SQL.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.fields.operators import OPERATOR_ARITY, Operator
from app.models.enums import SystemActionKind

__all__ = [
    "MAX_DEPTH",
    "MAX_NODES",
    "ActionNotPerformedNode",
    "ActionPerformedNode",
    "AssigneeChangedNode",
    "FieldRuleNode",
    "FilterNode",
    "GroupNode",
    "StatusChangedNode",
    "Within",
    "describe",
    "field_keys_in",
    "validate_shape",
]

#: A filter arrives from a customer's browser, so its size is an input to be
#: bounded rather than trusted. These are far above any filter a person builds
#: in the UI and far below what would hurt the planner or the recursion limit.
MAX_DEPTH = 10
MAX_NODES = 200


class _Node(BaseModel):
    """Shared config. `extra="forbid"` so a typo is an error, not a silent no-op.

    A filter that quietly ignores a misspelled key is worse than one that
    refuses: the user sees a result set, believes it answered their question,
    and acts on it.
    """

    model_config = ConfigDict(extra="forbid")


class Within(_Node):
    """A time window, expressed the way the user thought about it.

    `last_days` is relative and `from`/`to` are absolute; exactly one form is
    allowed. Relative windows are resolved against the *workspace's* timezone at
    compile time, not the server's (architecture rule 10) — "in the last 7 days"
    means seven of the customer's days.
    """

    last_days: int | None = Field(default=None, ge=1, le=3650)
    from_: dt.date | None = Field(default=None, alias="from")
    to: dt.date | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _exactly_one_form(self) -> Within:
        relative = self.last_days is not None
        absolute = self.from_ is not None or self.to is not None
        if relative and absolute:
            raise ValueError("a window is either last_days or from/to, not both")
        if not relative and not absolute:
            raise ValueError("a window needs last_days, or from, or to")
        if self.from_ and self.to and self.from_ > self.to:
            raise ValueError("window from is after to")
        return self


class FieldRuleNode(_Node):
    """One comparison against a lead's current value for one field.

    `key` is a `lead_fields.key`, never a column. The operator is checked
    against the *type registry* at compile time rather than here, because this
    module does not know which type the workspace gave that field — only the
    compiler, which has the field definitions loaded, can say whether `gt` is
    meaningful for it.
    """

    type: Literal["field"] = "field"
    key: str = Field(min_length=1, max_length=64)
    op: Operator
    #: Absent for `is_empty`/`is_not_empty`, a list for `in`/`has_any`, a pair
    #: for `between`, a scalar otherwise. Arity is checked below; the *type* of
    #: the value is checked by the compiler against the field's registry entry.
    value: Any = None

    @model_validator(mode="after")
    def _arity_matches_operator(self) -> FieldRuleNode:
        arity = OPERATOR_ARITY[self.op]
        if arity == 0:
            if self.value not in (None, "", [], {}):
                raise ValueError(f"{self.op.value} takes no value")
        elif arity == -1:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"{self.op.value} takes a non-empty list")
        elif arity == 2:
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError(f"{self.op.value} takes exactly two values")
        elif self.value is None:
            raise ValueError(f"{self.op.value} takes a value")
        return self


class _HistoryNode(_Node):
    """Common shape for the four predicates that query the timeline."""

    within: Within | None = None


class ActionPerformedNode(_HistoryNode):
    """ "Something happened" — compiles to `EXISTS` over `actions`.

    `min_count` above 1 turns the EXISTS into a counted subquery, which is how
    "at least 3 follow-ups" is expressed.
    """

    type: Literal["action_performed"] = "action_performed"
    action_kind: SystemActionKind | None = None
    #: Set when `action_kind` is CUSTOM — which of the workspace's own action
    #: types. Left open otherwise, so "any custom action" is expressible.
    action_type_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    #: Matched against the action's JSONB payload, key by key. This is how
    #: `{"direction": "OUTGOING"}` narrows CALL_LOGGED without the DSL knowing
    #: what a call is.
    payload_match: dict[str, Any] = Field(default_factory=dict)
    min_count: int = Field(default=1, ge=1, le=1000)


class ActionNotPerformedNode(_HistoryNode):
    """ "Nothing happened" — compiles to `NOT EXISTS`.

    The "no outgoing call in 14 days" case, and the single most valuable filter
    in a telecalling CRM: it is the one that finds neglected leads.

    No `min_count`: absence is absence. Note the window means "not in the last
    14 days", not "never" — a lead called a month ago still qualifies, which is
    the point.
    """

    type: Literal["action_not_performed"] = "action_not_performed"
    action_kind: SystemActionKind | None = None
    action_type_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    payload_match: dict[str, Any] = Field(default_factory=dict)


class StatusChangedNode(_HistoryNode):
    """A stage transition, matched on either end.

    Both ids are optional and mean different questions: `to` alone is "ever
    reached Lost", `from` alone is "ever left HOT", both is the full
    transition. Reads `payload->>'old_stage_id'` / `'new_stage_id'`, which is
    exactly what `actions_status_change_idx` covers — M5 built that index for
    this node.
    """

    type: Literal["status_changed"] = "status_changed"
    from_stage_id: uuid.UUID | None = None
    to_stage_id: uuid.UUID | None = None


class AssigneeChangedNode(_HistoryNode):
    """A reassignment, matched on either end.

    `from_membership_id` with a null `to` is "moved away from Priya" — the
    audit's example. Backed by `actions_assignment_idx`.
    """

    type: Literal["assignee_changed"] = "assignee_changed"
    from_membership_id: uuid.UUID | None = None
    to_membership_id: uuid.UUID | None = None


class GroupNode(_Node):
    """AND/OR over children, nestable.

    An empty group matches everything rather than nothing. That is the
    identity that makes "no filter" and "an empty filter" behave the same,
    which is what the list endpoint wants when a user clears the builder.
    """

    type: Literal["group"] = "group"
    op: Literal["AND", "OR"] = "AND"
    children: list[FilterNode] = Field(default_factory=list)


#: The discriminated union every consumer speaks. Discriminating on `type`
#: means a malformed node is rejected by Pydantic with the offending path,
#: rather than silently matching a more permissive variant.
FilterNode = Annotated[
    GroupNode
    | FieldRuleNode
    | ActionPerformedNode
    | ActionNotPerformedNode
    | StatusChangedNode
    | AssigneeChangedNode,
    Field(discriminator="type"),
]

GroupNode.model_rebuild()


def _walk(node: FilterNode, depth: int = 0) -> tuple[int, int]:
    """(node count, max depth) for one subtree."""
    if not isinstance(node, GroupNode):
        return 1, depth
    count = 1
    deepest = depth
    for child in node.children:
        child_count, child_depth = _walk(child, depth + 1)
        count += child_count
        deepest = max(deepest, child_depth)
    return count, deepest


def validate_shape(node: FilterNode) -> None:
    """Bound the tree before anything walks it again.

    Pydantic has already checked each node; this checks the *shape*. Called
    once at the boundary so the compiler can recurse without its own guard.
    """
    count, depth = _walk(node)
    if depth > MAX_DEPTH:
        raise ValueError(f"filter nests deeper than {MAX_DEPTH} levels")
    if count > MAX_NODES:
        raise ValueError(f"filter has more than {MAX_NODES} rules")


def field_keys_in(node: FilterNode) -> set[str]:
    """Every `lead_fields.key` the filter mentions.

    The permission gate uses this: each key must pass
    `FieldProjectionService.filterable` before the filter is compiled, because
    filtering on a hidden field is a read of it.
    """
    if isinstance(node, FieldRuleNode):
        return {node.key}
    if isinstance(node, GroupNode):
        keys: set[str] = set()
        for child in node.children:
            keys |= field_keys_in(child)
        return keys
    return set()


def describe(node: FilterNode) -> str:
    """A short human summary, for changeset summaries and the filter list.

    Deliberately structural rather than pretty — it names shapes, not labels,
    because this module has no access to a workspace's field labels or stage
    names. The frontend renders the readable version.
    """
    if isinstance(node, GroupNode):
        if not node.children:
            return "everything"
        joined = f" {node.op} ".join(describe(child) for child in node.children)
        return f"({joined})"
    if isinstance(node, FieldRuleNode):
        return f"{node.key} {node.op.value}"
    return node.type
