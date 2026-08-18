# API contract

Base `/api/v1`. JSON in/out. Timestamps ISO-8601 UTC with `Z`; clients render in
the workspace timezone.

**Tenant data lives under `/workspaces/{workspace_id}/…`.** Only `/auth/*`,
`/me/*`, `/workspaces` (list/create), and `/intake/*` sit outside that prefix.
The workspace scoping dependency resolves the path id, verifies the caller's
membership, loads their permission template, and injects a scoped session. A
handler that queries without it is a bug.

---

## Conventions

**Pagination** — `?limit=20&offset=0` (max 100) → `{items, total, limit, offset}`.

**Sorting** — `?sort=-created_at`. Sortable set = built-in columns plus the
workspace's **indexed fields**. Anything else → `400 field_not_indexed` with a
message naming the setting that fixes it.

**Errors** — `{"detail": {"code": "...", "message": "..."}}`.

| Status | When |
|---|---|
| 400 | malformed request, invalid DSL, non-indexed sort |
| 401 | missing/expired token |
| 403 | not a member · permission template denies · feature flag disabled |
| 404 | absent, soft-deleted, or in another workspace *(never 403 — don't leak existence)* |
| 409 | duplicate identity value · stage cardinality violation · lost-reason cap |
| 422 | validation failure, incl. field-type validation |
| 429 | rate limit (auth + intake) |

**Field permissions apply to every lead payload.** Responses contain only
View-granted fields; writes reject non-Edit fields with
`403 field_not_editable` naming them.

---

## Auth and workspaces

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login` | → `{access_token, refresh_token, user, memberships[]}` |
| POST | `/auth/refresh` | Rotates. Rate-limited. |
| POST | `/auth/logout` | Revokes the presented refresh token |
| GET | `/me` | User + memberships + workspaces |
| GET | `/me/permissions?workspace_id=` | Resolved capabilities + field grants — the frontend uses this to build the UI |
| POST | `/auth/change-password` | |
| GET | `/workspaces` | Workspaces the caller belongs to |
| POST | `/workspaces` | Provision per `01-data-model.md` §6 — structure only |
| GET / PATCH | `/workspaces/{id}` | Name, slug, preferences, feature flags |

---

## Settings

All under `/workspaces/{ws}/settings`. All require the `permissions` or
respective admin capability.

### Fields

| Method | Path | Notes |
|---|---|---|
| GET | `/settings/lead-fields` | `?search=&type=&include_hidden=` |
| POST | `/settings/lead-fields` | Label 1–40. `key` derived, then immutable. |
| PATCH | `/settings/lead-fields/{id}` | Label, description, properties, config, order |
| POST | `/settings/lead-fields/{id}/hide` · `/unhide` | Never delete |
| GET / POST | `/settings/lead-fields/{id}/options` | Label 1–70, colour, order |
| PATCH / DELETE | `/settings/lead-fields/{id}/options/{oid}` | Delete archives |
| POST | `/settings/lead-fields/{id}/options/bulk` | "Add multiple" — newline list |
| POST | `/settings/lead-fields/{id}/options/copy-from/{src}` | "Copy options" |
| PUT | `/settings/identity-field` | `{field_id}` → triggers `identity_value` backfill |
| PUT | `/settings/primary-fields` | `{h1_field_id, h2_field_id}` |
| GET / POST / DELETE | `/settings/indexed-fields` | Max 8. POST enqueues index build; returns `PENDING`. |
| GET | `/settings/field-types` | The 13-type registry with per-type config schema and operator list |

### Pipeline

| Method | Path | Notes |
|---|---|---|
| GET | `/settings/stages` | Grouped by kind |
| POST | `/settings/stages` | `kind=ACTIVE` only; singletons rejected `409` |
| PATCH | `/settings/stages/{id}` | Label (max 28), colour |
| DELETE | `/settings/stages/{id}` | Archives; ACTIVE only |
| PATCH | `/settings/stages/reorder` | `{ordered_ids[]}` within ACTIVE |
| GET | `/settings/stages/archived` | |
| GET / POST / PATCH / DELETE | `/settings/lost-reasons` | `409 lost_reason_limit` past 25 |

### Call dispositions

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/settings/call-dispositions` | |
| PATCH | `/settings/call-dispositions/{id}` | `403` if `is_system` |
| POST | `/settings/call-dispositions/{id}/set-default` | Clears the previous default |
| POST | `/settings/call-dispositions/{id}/archive` | Allowed on system entries |

### Custom actions

| Method | Path | Notes |
|---|---|---|
| GET | `/settings/custom-actions` | `?status=active\|archived&search=` |
| POST | `/settings/custom-actions` | Assigns the next workspace code from 1001 |
| PATCH | `/settings/custom-actions/{id}` | Name, icon, score, direction, allow-predated |
| POST | `/settings/custom-actions/{id}/archive` | |
| GET / POST | `/settings/custom-actions/{id}/fields` | Action-field builder |
| PATCH / DELETE | `/settings/custom-actions/{id}/fields/{fid}` | |
| GET | `/settings/action-field-types` | The 8-type registry |

### Permission templates

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/settings/permission-templates` | |
| GET / PATCH | `/settings/permission-templates/{id}` | `403` if `is_readonly` (Root) |
| DELETE | `/settings/permission-templates/{id}` | `409` if assigned |
| GET | `/settings/permission-templates/{id}/field-grants` | Matrix, plus per-column rollups |
| PUT | `/settings/permission-templates/{id}/field-grants` | `{grants: [{field_id, view, edit, import, export}]}` — full replace |
| PUT | `/settings/permission-templates/{id}/field-grants/bulk` | `{grant, value, field_ids?}` — column select-all |
| GET / POST / DELETE | `/settings/permission-templates/{id}/assignees` | |

### Members, licensing, availability

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/members` | Invite by email |
| PATCH | `/members/{id}` | Template, manager, name |
| POST | `/members/bulk-upload` | Excel → job → mapping → commit |
| POST | `/members/{id}/license` · `DELETE` | Assign / revoke a seat. `409 seat_limit_reached`. |
| PUT | `/members/{id}/availability` | `{status, note}` — WORKING / ON_LEAVE / INACTIVE |
| GET | `/members/{id}/availability-log` | |
| POST | `/members/{id}/deactivate` | **Body requires `reassign_to_membership_id`.** Refuses with `409 reassignment_required` if the member holds open leads and none is given. |
| POST | `/members/{id}/reactivate` | Requires a free seat |
| GET | `/members/hierarchy` | Manager tree |

An unlicensed or inactive member is rejected at login with
`403 no_license` / `403 member_inactive`. A manager sees their reports' leads —
enforced in the scoping layer, not per endpoint.

### Sales groups

| Method | Path |
|---|---|
| GET / POST | `/settings/sales-groups` |
| PATCH / DELETE | `/settings/sales-groups/{id}` |
| PUT | `/settings/sales-groups/{id}/members` — `[{membership_id, weight}]` |

### Assignment rules

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/settings/assignment-rules` | |
| PATCH / DELETE | `/settings/assignment-rules/{id}` | |
| PATCH | `/settings/assignment-rules/reorder` | Priority order |
| POST | `/settings/assignment-rules/preview` | `{lead}` → which rule matches and who it would assign to. Dry-run; assigns nothing. |
| POST | `/leads/distribute` | Redistribute a filtered set. `{filter, strategy, config}` → job. Writes one changeset. |

Rules run on every lead create — UI, import, and intake — in priority order,
first match wins, skipping members whose availability is not `WORKING` when
`skip_unavailable` is set.

### Message templates

| Method | Path | Notes |
|---|---|---|
| GET | `/templates` | `?channel=` — returns personal + shared + the caller's role-scoped |
| POST / PATCH / DELETE | `/templates`, `/templates/{id}` | Role-scoped creation needs the permissions capability |
| POST | `/templates/{id}/render` | `{lead_id}` → rendered subject/body plus `unresolved[]`. Substitution runs through `FieldProjectionService`, so a template can't leak a field the caller lacks View on. |

---

## Leads

| Method | Path | Notes |
|---|---|---|
| GET | `/leads` | Quick filters, `?q=`, `sort`, `columns`. **Never returns actions.** |
| POST | `/leads/search` | Ad-hoc filter DSL in body |
| POST | `/leads` | `409 duplicate_identity` on the workspace identity field |
| GET | `/leads/{id}` | Full record, View-projected |
| PATCH | `/leads/{id}` | Writes one `FIELD_CHANGE` per changed field. `lock_after_create` fields rejected. |
| DELETE | `/leads/{id}` | Soft delete |
| GET | `/leads/{id}/neighbors` | `{prev_id, next_id, position, total}` within the filter |
| POST | `/leads/bulk` | Max 500; per-field permission checks apply |
| POST | `/leads/merge` | `{primary_id, merge_ids[]}` |
| GET | `/leads/duplicates` | Grouped by identity value |
| POST | `/leads/import` | → `{job_id}`. Mapping limited to Import-granted, `show_in_import` fields. |
| GET | `/leads/export` | → `{job_id}`. Export-granted fields only. |

Lead payload shape:

```jsonc
{
  "id": "uuid",
  "identity_value": "919876543210",
  "primary": { "h1": "Sriya Misra", "h2": "919876543210" },
  "stage": { "id": "uuid", "label": "Interview Scheduled", "kind": "ACTIVE", "color": "#22c55e" },
  "lost_reason": null,
  "assignee": { "membership_id": "uuid", "full_name": "Rushmitha" },
  "rating": 4,
  "score": 72,
  "values": { "<field_key>": <typed value> },   // View-granted only
  "labels": [...],
  "last_action_at": "...",
  "created_at": "..."
}
```

---

## Actions

| Method | Path | Notes |
|---|---|---|
| GET | `/leads/{id}/actions` | Filters: kind, action_type_id, actor, from, to |
| POST | `/leads/{id}/actions` | Manual kinds + `CUSTOM`. Payload validated against `action_fields`. Predated rejected unless `allow_predated`. |
| PATCH | `/actions/{id}` | `body`, `is_pinned` only; author, within 24h |
| POST | `/actions/{id}/attachments` | Multipart for `FILE` fields → S3 |
| GET | `/actions` | Cross-lead feed for reports |

Call logging is manual: `kind=CALL_LOGGED` with `{direction, disposition_id,
duration_seconds, notes}`. The client preselects the default disposition when
duration exceeds `connected_call_min_seconds`.

WhatsApp is a client-side `wa.me` deep link followed by a `WHATSAPP_SENT` action.
**Nothing in the response may imply delivery.**

---

## Filters, layouts, labels, tasks

`/filters` (CRUD, duplicate, reorder, `/stats`) · `/layouts?filter_id=` ·
`/labels` + `/leads/{id}/labels/{lid}` · `/tasks` (CRUD, buckets
`upcoming|late|done`) · `/leads/{id}/tasks` · `POST /tasks/bulk-upload` (Excel).

Filters carry `visibility ∈ PERSONAL | SHARED | ROLE` (+ `template_id` for ROLE).
System filters are read-only → `403 system_filter_readonly`.

### History predicates in the filter DSL

Beyond field rules, four node types query the **timeline** rather than current
state. Full shapes in `01-data-model.md` §6.1.

| Node `type` | Answers |
|---|---|
| `action_performed` | "logged a call in the last 7 days", "≥3 of custom action X" |
| `action_not_performed` | "no outgoing call in 14 days" — the follow-up-chasing filter |
| `status_changed` | "went from HOT to Lost last week" (`from`/`to` both optional) |
| `assignee_changed` | "moved off Priya this month" |

All accept `within: {last_days}` or `{from, to}`. They compile to
`EXISTS`/`NOT EXISTS` over `actions` and rely on the expression indexes defined in
`01-data-model.md` §6.1. The filter builder renders them as first-class rules —
users must never see raw JSON.

---

## Changesets and undo

| Method | Path | Notes |
|---|---|---|
| GET | `/changesets` | The edit report. Filter by source, actor, date. |
| GET | `/changesets/{id}` | Summary plus the actions it produced |
| POST | `/changesets/{id}/preview-undo` | Per-lead: reversible, conflicted (changed since), or already-undone |
| POST | `/changesets/{id}/undo` | `{skip_conflicts: bool}`. Reverses atomically and records a new changeset with `undo_of_id`. |

Only `FIELD_CHANGE`, `STAGE_CHANGE`, `ASSIGNMENT_CHANGE` and `RATING_CHANGE` are
reversible. A lead edited after the changeset is a **conflict** — surfaced, never
silently clobbered.

---

## Dashboard and reports

`/dashboard/follow-ups` · `/dashboard/leads-by-stage` · `/dashboard/filter-stats`
`/reports/leaderboard` · `/reports/activity` · `/reports/funnel` ·
`/reports/breakdown?field_key=` — the last one replaces a fixed "sources" report,
because *which* field represents source is a per-workspace decision.

All take `from` / `to` and optional `assignee_id`. Budget: under 500ms on the
demo workspace.

### Custom dashboards

| Method | Path | Notes |
|---|---|---|
| GET | `/dashboards` | Personal + shared + the caller's role-bound |
| POST / PATCH / DELETE | `/dashboards`, `/dashboards/{id}` | `layout` is `[{widget, x, y, w, h, config}]` |
| PUT | `/dashboards/{id}/default` | The caller's landing dashboard |
| GET | `/dashboards/widgets` | Widget catalogue with each one's config schema |

Binding a dashboard to a `template_id` makes it role-specific — every member on
that permission template gets it.

### Scheduled reports

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/scheduled-reports` | `{report_type, params, cron, recipients[], format}` |
| PATCH / DELETE | `/scheduled-reports/{id}` | |
| POST | `/scheduled-reports/{id}/run-now` | Sends immediately |

`cron` is evaluated in the **workspace** timezone. Reports render **as the
creating member**, so field permissions govern what reaches the inbox.

### Recurring dates

| Method | Path | Notes |
|---|---|---|
| GET | `/recurring-dates/occurrences` | `?field_key=&from=&to=` — upcoming occurrences across leads |

Automated greetings are a scheduled job pairing this with a message template.

---

## Intake and outbound

### `POST /intake/leads` — `X-API-Key`

```jsonc
{
  "identity": "919876543210",
  "values": { "<field_key>": <value> },
  "stage": "<stage label or id>",
  "assignee_email": "...",
  "dedupe": "update"     // update | skip | create_duplicate
}
```

- Identity matched on the workspace's identity field, normalised with its country
  code
- `dedupe: update` merges non-null values; never blanks existing data
- **Unknown keys are accepted, stored, and returned in `warnings`.** Never reject
  a payload for an unknown field — a rejected payload at 2am is a lost lead.
- Unknown stage or option → `400`, logged
- Every request logged to `intake_log`, rejections included
- 100 req/min per key

Also `POST /intake/actions`, `POST /intake/leads/batch` (≤500).

### Outbound

`/settings/webhooks` CRUD + `/test` · `/settings/api-keys` (plaintext once) ·
`/settings/outbox` with `/retry` · `/settings/intake-log`.

Envelope:

```jsonc
{
  "event": "lead.stage_changed",
  "event_id": "uuid",
  "workspace_id": "uuid",
  "occurred_at": "...",
  "data": { "lead_id": "...", "old_stage": "...", "new_stage": "...", "actor": {...} }
}
```

Headers `X-CRM-Event`, `X-CRM-Event-Id` (stable across retries), and
`X-CRM-Signature: sha256=…`. Events: `lead.created` · `lead.updated` ·
`lead.stage_changed` · `lead.assigned` · `lead.field_changed` · `action.created` ·
`task.created` · `task.completed`.

---

## Permission enforcement — test matrix

Write an explicit test for each. These are the ones that bite.

| Scenario | Expected |
|---|---|
| Member of workspace A requests a lead id in workspace B | `404` |
| Template lacks `View` on field F | F absent from list, detail, export, webhook |
| Template lacks `Edit` on field F | PATCH including F → `403 field_not_editable` |
| Template lacks `Export` on any field | Export job → `403` |
| Template lacks `Import` on field F | F not offered in the mapping UI; forced map → `400` |
| Feature flag `campaign` off | Campaign endpoints → `403 feature_disabled` |
| Root template edit attempt | `403 template_readonly` |
| Second `WON` stage creation | `409 stage_cardinality` |
| 26th lost reason | `409 lost_reason_limit` |
| Sort on a non-indexed field | `400 field_not_indexed` |
