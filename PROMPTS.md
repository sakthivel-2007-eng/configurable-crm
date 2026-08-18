# Claude Code prompts

**Revision 2** — matches the 12-milestone plan in `docs/00-milestones.md`.

One prompt per milestone. Paste verbatim, run to completion, review, commit, move
on. **Never paste two at once.** The failure mode on this project is building
ahead of the spec.

---

## Setup

```bash
mkdir configurable-crm && cd configurable-crm && git init
# copy CLAUDE.md, PROMPTS.md and docs/ into the repo root
claude
```

Run this **before M0**, every time you start a fresh Claude Code session:

```
Read CLAUDE.md and all of docs/, starting with docs/03-configuration-model.md.

Summarise back in under 250 words: what makes this product different from a
single-tenant CRM, the three architecture rules most likely to be violated during
this build, and anything ambiguous or contradictory across the documents.

Then answer one question: name three places where it would be tempting to
hardcode a business taxonomy, and how the design prevents it.

Do not write any code.
```

If that last answer is weak, fix the docs before writing a line.

---

## M0 — Scaffold

```
Implement milestone M0 from docs/00-milestones.md. Scaffold only — no domain
models, nothing from M1.

docker-compose with postgres 16, redis, minio, api and web. FastAPI with
pydantic-settings config and an async SQLAlchemy engine. Alembic initialised with
no revisions yet. A /health endpoint reporting db, redis and object-storage
connectivity. Vite + React + TS + Tailwind + shadcn/ui with a router and one page
that calls /health and renders the result.

Add ruff, mypy strict on app/, eslint, prettier and pre-commit hooks. Configure
pytest with a testcontainers Postgres fixture and one passing test. Configure
Playwright with one smoke test. Add a GitHub Actions workflow running lint,
typecheck and tests. Write README.md.

Verify by running docker compose up from clean and confirming /health is green.
```

---

## M1 — Tenancy, auth, user lifecycle

```
Implement M1. Multi-tenancy is the foundation — get it wrong and everything after
is unsafe.

Create workspaces, users, memberships and a permission_templates shell per
docs/01-data-model.md §2, plus licensing and availability per §5.1.

Auth: argon2id, JWT access (30 min) and refresh (30 days) with rotation, login
rate limiting. Reject login with 403 no_license for an unlicensed member and
403 member_inactive for a deactivated one.

Build the workspace scoping dependency: resolve workspace_id from the path,
verify membership, load the permission template, inject a scoped session. Make it
structurally hard to bypass — if a repository method can be called without a
workspace scope, change the signature so it can't compile.

Team hierarchy: memberships.manager_id, and the visibility rule that a manager
sees their reports' leads. Implement that in the scoping layer, not per endpoint.

User lifecycle endpoints per docs/02-api-contract.md "Members, licensing,
availability": bulk upload via Excel, assign/revoke licence against the workspace
seat limit, availability (WORKING / ON_LEAVE / INACTIVE) with a log, reactivate,
and deactivate. Deactivate MUST require reassign_to_membership_id when the member
holds open leads — return 409 reassignment_required otherwise. Never orphan a
pipeline.

POST /workspaces provisioning exactly per docs/01-data-model.md §7: 4 built-in
fields, 4 stages, 5 lost reasons, 7 system call dispositions, 5 permission
templates. STRUCTURE ONLY — no industry fields, no product lists, no business
statuses. If you catch yourself writing a domain term, stop.

Frontend: login, workspace picker, auth context, token-refresh interceptor,
members admin.

Then write the cross-workspace isolation test suite: for every endpoint that
exists, a member of workspace A gets 404 — not 403 — for workspace B's data, by
direct id, by list, and by filter. This suite grows every milestone.
```

---

## M2 — Field definition engine

```
Implement M2 — the field definition engine. This is the heart of the product.

Create lead_fields, field_options, action_fields and action_field_options per
docs/01-data-model.md §3.1 and §3.4.

Build TWO type registries in code: 13 lead field types and 8 action field types
(docs/03-configuration-model.md §1.3 and §4.3). Each registry entry declares
validation, normalisation, JSONB storage shape, supported filter operators, and a
renderer contract the frontend consumes. Expose them at GET
/settings/field-types and /settings/action-field-types so the frontend never
hardcodes the list.

Before implementing the three composites, show me your storage and query design
for each:
- DEPENDENT_DROPDOWN: parent field reference, option tree, cascade on
  read/write/filter/import
- RECURRING_DATE: recurrence rule plus a derived next-occurrence used for
  filtering and reminders
- LOCATION: structured address with optional lat/lng — not a scalar

Field properties: show_in_import, show_in_quick_add, lock_after_create,
can_use_variable. For can_use_variable, resolve {{...}} templates at write time
and quarantine unresolved ones — never persist a raw template as a value. The
system we're replacing did exactly that and corrupted its own taxonomy.

Add indexed_fields plus a background worker issuing CREATE INDEX CONCURRENTLY
with generated safe names. That worker is the ONLY place DDL is emitted at
runtime. Never ALTER TABLE.

Add GET /recurring-dates/occurrences — upcoming occurrences across leads.

Settings UI: field list with search, type filter and hidden view; create/edit
drawer; option editor with per-option colours, drag reorder, Copy options and Add
multiple; identity-field and H1/H2 primary-field pickers.

Verify: create one field of each of the 13 types through the UI, render a lead
form for all of them, and demonstrate a dependent dropdown cascading.
```

---

## M3 — Pipeline and taxonomy settings

```
Implement M3 per docs/03-configuration-model.md §2, §3, §4 and §5.

Stages with ENFORCED cardinality: exactly one live INITIAL, one WON and one LOST
per workspace, N ACTIVE. Use the partial unique index in docs/01-data-model.md
§3.2 and return 409 stage_cardinality. Lost reasons capped at 25 with a clear
409, not a constraint error. Labels max 28 chars, per-stage colour, archive
rather than delete, with an archived list.

Call dispositions: exactly one default per workspace, system entries archivable
but not editable, drag reorder, archived section.

Custom action types: workspace-sequential code from 1001, score between -1000 and
1000, direction (Inbound/Outbound/Information), allow_predated, and nested action
fields reusing the M2 field builder component.

Workspace preferences: country code, timezone, currency, connected-call minimum
duration, session timeout, leaderboard metrics, feature flags, smart syncing.

Feature flags must gate ENDPOINTS with 403 feature_disabled, not just hide
navigation. Add a test proving a disabled feature's API refuses.

Settings UI: three-column stage pipeline with drag reorder and colour pickers,
disposition list with the per-item menu, custom action builder.
```

---

## M4 — Permissions

```
Implement M4. Build this BEFORE any lead endpoint — retrofitting field-level
permissions across a finished API is not feasible.

permission_templates with a Pydantic-validated capabilities model. Spec the
CONTENTS of all 10 Access groups (Leads, Salesform, Team, Permissions, Calling,
Reports, Automations, Tasks, Billings, Integrations) and all 3 View groups (Lead,
Dashboard, Leads Table) — not just their names. Where the source system's
contents are unknown, propose a set and flag it for review.

template_field_grants: one row per (template, field, grant) across VIEW, EDIT,
IMPORT, EXPORT. Presence means granted, absence means denied. Deny by default.

Then the two services everything else depends on:
- FieldProjectionService — every lead read passes through it and it strips
  non-View fields. List, detail, export, webhook payloads, reports, template
  rendering. One implementation, no exceptions.
- FieldWriteFilter — every lead write passes through it. Non-Edit fields are
  REJECTED with 403 field_not_editable naming them. Never silently dropped.

Implement "Set up your lead view": a per-template lead-detail layout defining
which fields appear, in what order, and which groups start collapsed.

Cache resolved permissions per request. Root templates are read-only (403).

Template editor UI: Assignee, Access, View, and the field matrix — fields against
View/Edit/Import/Export with column select-all, per-column filters, live counts,
rollup badges (Full/Partial/None) and row search.

Verify with all four: a field granted View but not Edit must appear in detail, be
rejected on PATCH, be absent from export, and be absent from webhook payloads.
```

---

## M5 — Leads, timeline, templates

```
Implement M5. Every read goes through FieldProjectionService and every write
through FieldWriteFilter — no exceptions, no shortcuts for "internal" calls.

leads with JSONB values keyed by lead_fields.key, identity_value denormalised
from the workspace's identity field plus a backfill job for when that setting
changes, dedup on identity_value, and phone normalisation using the workspace's
default country code — never a hardcoded 91.

Write the action-writing service and its tests FIRST, before any endpoint.

Three things must be designed in NOW because they cannot be retrofitted:

1. CHANGESETS (docs/01-data-model.md §4.2). Every mutation batch — single PATCH,
   bulk edit, import, distribution, intake — opens a changeset, and every action
   it produces carries changeset_id. This is what makes undo possible in M7.

2. STATUS_CHANGE and ASSIGNMENT_CHANGE payloads must carry old and new ids, with
   the expression indexes in docs/01-data-model.md §6.1 created in this
   milestone. M6's history filters depend on them.

3. score_applied snapshotted on each action, so editing a custom action type's
   score later doesn't rewrite history. Lead score is the sum.

Every mutation appends, in the same transaction: one FIELD_CHANGE per changed
field with old and new values, plus STAGE_CHANGE, ASSIGNMENT_CHANGE or
RATING_CHANGE. Entering the LOST stage requires a lost reason; leaving clears it.
lock_after_create fields are rejected on update.

Manual actions: note, call logged (disposition from the workspace list, default
preselected above connected_call_min_seconds), WhatsApp, email, SMS. There is no
telephony in this product — do not build a provider interface or a stub.

Custom action logging: dynamic form generated from action_fields, FILE fields
uploading to S3, predated timestamps rejected unless allow_predated.

MESSAGE TEMPLATES (docs/01-data-model.md §5.4): personal, shared and role-scoped
templates per channel with {{field_key}} substitution. POST /templates/{id}/render
resolves against a lead THROUGH FieldProjectionService, so a template cannot leak
a field the sender lacks View on, and reports unresolved placeholders. The
WhatsApp/SMS/email compose flows pick from them.

Frontend: lead detail as an overlay over the list preserving filter context, the
field grid honouring the M4 lead view, timeline with kind/time/actor filters,
prev/next within the filtered set.

Verify: edit three fields at once and show me three FIELD_CHANGE rows sharing one
changeset id; render a WhatsApp template against a real lead and show the preview
plus any unresolved placeholders.
```

---

## M6 — Lead list, filters, history filters

```
Implement M6.

List endpoint with quick filters, pagination, column hydration, and sorting
restricted to built-in columns plus the workspace's indexed fields — anything
else returns 400 field_not_indexed with a message naming the setting that fixes
it.

Filter DSL compiled to parameterised SQLAlchemy, never string interpolation,
nested AND/OR to arbitrary depth. Field operators come from the M2 type registry,
so a DATE field offers date operators and a TAGS field offers set operators — the
builder must not hardcode them.

THE SIGNIFICANT ADDITION — history predicates (docs/01-data-model.md §6.1 and
docs/02-api-contract.md). Four node types that query the timeline, not current
state:
- action_performed (kind, action_type_id, actor, min_count, within)
- action_not_performed — the "no outgoing call in 14 days" case
- status_changed (from, to, within — from and to each optional)
- assignee_changed (from, to, within)

They compile to EXISTS / NOT EXISTS correlated subqueries over actions, using the
expression indexes built in M5. Show me EXPLAIN ANALYZE for
action_not_performed over the 50k demo workspace before you consider this done —
that one is the most likely to table-scan.

Search over search_vector plus trigram on identity_value.

saved_filters CRUD with visibility (PERSONAL / SHARED / ROLE), stats and reorder;
table_layouts per member per filter.

Frontend: TanStack Table with server pagination, column picker with drag reorder,
quick-filter bar, and a visual filter builder that renders history predicates as
FIRST-CLASS RULES with their own controls. Users must never see raw JSON.

Verify both of these through the builder: "status went from HOT to Lost in the
last 7 days" and "no outgoing call in 14 days". And p95 under 300ms on the demo
workspace.
```

---

## M7 — Tasks, bulk, import/export, undo

```
Implement M7.

Tasks with late/upcoming/done buckets, TASK_CREATED / TASK_COMPLETED actions
through the M5 service, and bulk task upload via Excel. Labels and lists.

Bulk edit capped at 500, permission-checked per field, opening ONE changeset per
run.

EDIT REPORT & UNDO (docs/02-api-contract.md "Changesets and undo"):
- GET /changesets — the edit report, filterable by source, actor and date
- POST /changesets/{id}/preview-undo — per lead: reversible, conflicted, or
  already undone
- POST /changesets/{id}/undo — reverses atomically by replaying inverse field
  changes, recording a new changeset with undo_of_id
A lead edited after the changeset is a CONFLICT. Surface it and let the operator
choose. Never silently clobber a later edit.

Import: CSV/XLSX to a job, mapping UI offering only fields the caller has Import
on AND that have show_in_import, dry-run preview showing create-versus-update
counts, then commit as one changeset.

Excel Advance Distribution: distribute an imported batch by round-robin,
weighted, or availability-aware strategy.
Owner Specific Assignment: assignee resolved from a column in the sheet.
Excel Bulk Update: update-by-identity, distinct from create.
Import existing Actions: historical timeline migration with its own mapping UI —
map columns to action kinds, custom action types, timestamps and actors.

Export honours Export grants. Duplicates view grouped by identity value, with
merge.

Verify: bulk-edit 300 leads, then fully undo it, and show the conflict handling
when one of those leads was edited in between.
```

---

## M8 — Assignment engine, sales groups, scheduler

```
Implement M8 per docs/01-data-model.md §5.2, §5.3 and §5.5.

Sales groups: named groups of members with per-member weights, usable as
distribution targets and report segments.

Assignment rules: priority-ordered, each with filter-DSL conditions evaluated
against the NEW lead and a strategy of ROUND_ROBIN, WEIGHTED, FIELD_VALUE,
SALES_GROUP, FIXED or UNASSIGNED. First match wins. skip_unavailable skips
members whose availability is not WORKING. Round-robin state lives in
assignment_cursors and must be correct under concurrent inserts — use a row lock,
not a read-then-write.

Rules run on EVERY lead create path: UI, import, and the intake API. Add
POST /settings/assignment-rules/preview as a dry run that assigns nothing.

POST /leads/distribute redistributes a filtered set as a job, writing one
changeset so it can be undone.

Scheduler (arq cron), evaluated in the WORKSPACE timezone, not the server's:
- Scheduled report email: saved report type + params + cron + recipients +
  format. Rendered AS THE CREATING MEMBER so field permissions govern what
  reaches the inbox.
- Recurring-date greetings: pair /recurring-dates/occurrences with an M5 message
  template.

Outbound email transport (SMTP or provider), needed by both.

Verify: post a lead to the intake API and show it auto-assigned by the configured
rule, skipping a rep marked ON_LEAVE. Then show a scheduled report arriving by
email with a restricted member's field permissions applied.
```

---

## M9 — Dashboards and reports

```
Implement M9. Read the dataviz skill before writing any chart code.

All report endpoints from docs/02-api-contract.md: leaderboard honouring the
workspace's configured leaderboard metrics, call report, activity, funnel, and
breakdown?field_key= — the last one is parameterised because which field
represents "source" is a per-workspace decision, not a fixed column.

Each endpoint under 500ms on the demo workspace, with tests asserting it. If a
query can't hit that, precompute rather than shipping something slow.

Build ONE generic pivot widget with a configurable row dimension and stage-kind
columns. If you write a second similar component, stop and generalise the first.

CUSTOM DASHBOARDS: user-composed layouts — add, remove, arrange, resize and
configure widgets, saved per member. GET /dashboards/widgets returns the widget
catalogue with each widget's config schema, so the frontend doesn't hardcode it.

ROLE-SPECIFIC DASHBOARDS: binding a dashboard to a permission template gives it
to every member on that template.

Date ranges persist in the URL. Drill-through from any cell into the lead list
with the equivalent filter applied.

Verify: build a dashboard as admin, bind it to the Caller template, log in as a
caller and confirm they get it; then click a cell showing N and confirm the list
says exactly N.
```

---

## M10 — Intake and event bus

```
Implement M10.

POST /intake/leads with API-key auth. Match on the workspace's identity field,
normalised with its country code. All three dedupe modes. Assignment rules from
M8 run on intake-created leads.

Critically: unknown keys in values are ACCEPTED, stored, and returned in
warnings — never rejected. A rejected payload at 2am is a lost lead. Log every
request to intake_log including rejections, with an admin viewer.

Transactional outbox: write outbox_events in the same transaction as the state
change. An arq worker delivers with HMAC-SHA256 signing, exponential backoff of
2^attempts minutes capped at 60, DEAD after 8 attempts. X-CRM-Event-Id stable
across retries so consumers can dedupe. Webhook payloads pass through
FieldProjectionService using the endpoint's configured role.

Admin UI: webhooks with test-send, api-keys showing the plaintext key exactly
once, outbox with manual redrive.

Verify: kill the worker mid-delivery and show a retry rather than a lost event;
post an unknown field and show it stored with a warning.
```

---

## M11 — Hardening, provisioning, deploy

```
Implement M11.

Self-serve workspace signup. Performance pass at 500k leads across 5 workspaces —
show before/after numbers for the lead list, history filters, dashboards and
reports. Fix regressions rather than relaxing thresholds.

structlog JSON logging with request and workspace ids, Sentry, and Prometheus
metrics for request duration, outbox depth, intake rate, index-build queue depth
and scheduler lag.

Re-verify the full permission test matrix at the end of docs/02-api-contract.md.

Playwright E2E that proves the core claim: create a brand-new workspace, add
custom fields of several types, build a pipeline, create a permission template
with a restricted field, set up an assignment rule, invite a member, then log in
as them and confirm they see exactly what the template allows and receive an
auto-assigned lead — all through the UI, no SQL, no code changes.

Nightly backups, then actually perform a restore drill into a scratch database
and report what happened. Deploy staging and production. Write an operator
runbook: restore, redrive a dead event, rotate an API key, add a workspace,
rebuild an index, unstick the scheduler.
```

---

## Recovering when it goes sideways

**Hardcoded taxonomy appears:**

```
You've hardcoded business taxonomy. This is a multi-tenant product — {term} is
customer data, not product code. Find every instance in the diff, remove them,
and make the value come from workspace configuration.
```

**Field permissions bypassed:**

```
This path reads or writes lead fields without going through
FieldProjectionService / FieldWriteFilter. Route it through, and add a test
proving a non-View field is absent from this response.
```

**Workspace scoping bypassed:**

```
This query doesn't filter on workspace_id. Fix it, change the repository
signature so this class of bug can't compile, and add the isolation test.
```

**Changeset missing:**

```
This mutation path doesn't open a changeset, so M7 can't undo it. Route it
through the changeset-aware action-writing service and add a test asserting every
action it produces shares one changeset_id.
```

**Built ahead of spec:**

```
You implemented things outside M{n}. List what you added that isn't in its scope
in docs/00-milestones.md, then remove it.
```

**Slow endpoint:**

```
{endpoint} is over budget on the demo workspace. Show me EXPLAIN ANALYZE for the
query it runs. Diagnose from the plan before changing anything, then fix the root
cause — not by caching or raising the threshold.
```

**Context drift on a long milestone:**

```
Re-read CLAUDE.md and docs/00-milestones.md section M{n}. List what's done, what
remains, and anything built that isn't in scope.
```
