# Milestones

**Revision 2** — expanded after the coverage audit in `04-feature-coverage.md`.
The first version covered ~51% of TeleCRM's documented functionality. This one
folds in the 22 missing items.

Twelve milestones. Configuration engine first, CRM second — that order is not
negotiable (`03-configuration-model.md` §7).

Every milestone's definition of done includes: lint clean, types clean, tests
pass, **cross-workspace isolation tested**, no TODOs in the diff.

---

## Scope ladder

| Rung | Contents | Status |
|---|---|---|
| **v1 — Configurable CRM** | Tenancy · user lifecycle & licensing · settings engine · permission templates with field-level RBAC · leads · timeline · list/filters **incl. history filters** · message templates · assignment engine · sales groups · tasks · dashboards · reports · import/export · intake + outbox | **This plan. M0–M11.** |
| **v1.5 — Growth surface** | Campaigns (calling queues) · Salesform (public capture) · integrations marketplace · merge tooling depth | Next |
| **v2 — Automation** | Node-graph workflow engine: versioning, branching, durable sleep, wait-for-reply, execution log | **Multi-month.** Ship v1 with webhooks + n8n. |
| **v2 — USP** | AI voice-agent integration | Attaches to the M10 event bus |

Campaigns being v1.5 is the one call worth re-checking: they're the calling queue
a telecaller works from. Without them, reps work from saved filters. Workable,
but confirm you're happy with it.

---

## M0 — Scaffold *(2–3 days)*

docker-compose (postgres, redis, minio, api, web) · FastAPI + pydantic-settings +
async SQLAlchemy · Alembic · `/health` · Vite + React + TS + Tailwind +
shadcn/ui · ruff, mypy strict, eslint, prettier, pre-commit · pytest with
testcontainers · Playwright · GitHub Actions · README.

**Done when** a fresh clone runs `docker compose up`, `/health` is green, CI passes.

---

## M1 — Tenancy, auth, user lifecycle *(6–8 days)*

*Expanded — was 4–6. Adds the whole Team & License section.*

- `workspaces`, `users`, `memberships`, `permission_templates` shell
- Argon2id, JWT access + refresh with rotation, login rate limiting
- **Workspace scoping dependency** — path → membership check → scoped session
- `POST /workspaces` provisioning per `01-data-model.md` §6, structure only
- **Team hierarchy** — `manager_id`, plus the visibility rule that a manager sees
  their reports' leads. This is a data-access rule, not a UI tree.
- **Licensing** — `licenses` table, seat count per workspace, assign/revoke,
  block login when unlicensed
- **Availability** — Working / On Leave / Inactive per membership, with history
- **Deactivate user + reassign leads** — a required reassignment step, never
  orphaned pipeline
- **Reactivate user**
- **Bulk user upload via Excel**
- Frontend: login, workspace picker, auth context, members admin

**Done when** a user in workspace A provably cannot read anything in workspace B —
by id, list, or filter — and deactivating a rep with 500 leads forces a
reassignment before it completes.

---

## M2 — Field definition engine *(6–8 days)*

Unchanged. `lead_fields`, `field_options`, `action_fields`,
`action_field_options` · **two type registries** (13 lead, 8 action) exposed via
API · the three composites designed explicitly (`DEPENDENT_DROPDOWN`,
`RECURRING_DATE`, `LOCATION`) · field properties incl. **variable resolution at
write time** · `indexed_fields` + `CREATE INDEX CONCURRENTLY` worker · settings
UI · identity field and H1/H2 pickers.

**Adds:** a **recurring-date occurrences view** — upcoming occurrences across
leads, filterable. (Automated birthday greetings are automation; they land in
M8 with templates + scheduler.)

**Done when** an admin creates one field of each of the 13 types through the UI, a
lead form renders all of them, and a dependent dropdown cascades.

---

## M3 — Pipeline and taxonomy settings *(4–6 days)*

Unchanged. Stages with enforced cardinality · lost reasons capped at 25 · call
dispositions with one default and system/custom tiers · custom action types with
code, score, direction, allow-predated, nested fields · workspace preferences ·
**feature flags gating endpoints with 403**.

---

## M4 — Permissions *(7–9 days)*

*Expanded — was 6–8.*

- `permission_templates` with validated `capabilities` across all 10 Access
  groups and 3 View groups — **each group's contents spec'd, not just named**
- `template_field_grants` — View/Edit/Import/Export, deny by default
- **`FieldProjectionService`** and **`FieldWriteFilter`** — the single read and
  write chokepoints
- **"Set up your lead view"** — per-template lead-detail layout: which fields,
  which order, which groups collapsed
- Template editor UI incl. the field matrix with column select-all, filters,
  rollups, search

**Done when** a View-not-Edit field is: visible in detail, rejected on PATCH,
absent from export, absent from webhook payloads. All four tested.

> Build before the first lead endpoint. Non-negotiable.

---

## M5 — Leads, timeline, templates *(8–10 days)*

*Expanded — was 6–8. Adds message templates and changesets.*

- `leads` with JSONB values, `identity_value` + backfill, dedup, workspace-aware
  phone normalisation
- **Action-writing service, written first**: one `FIELD_CHANGE` per changed field
  with old/new, plus `STAGE_CHANGE` / `ASSIGNMENT_CHANGE` / `RATING_CHANGE`
- **`changesets`** — every mutation batch gets a changeset id stamped on the
  actions it produced. **This is what makes bulk-edit undo possible in M7.**
  Design it in now; it cannot be retrofitted.
- **`STATUS_CHANGE` payload indexed on old and new value** — required for the
  transition filters in M6
- Manual actions: note, call logged (workspace dispositions, default applied
  above the connected-call threshold), WhatsApp, email, SMS
- Custom action logging: dynamic form from `action_fields`, FILE → S3,
  `score_applied` snapshot, lead score rollup
- **Message templates** — new entity. Personal and role-scoped, per channel
  (WhatsApp / SMS / Email), with `{{field}}` substitution from the lead's values,
  a preview, and permission-scoped visibility. The compose flows use them.
- Frontend: lead detail overlay respecting the M4 lead view, timeline with
  filters, prev/next within the filtered set

**Done when** editing three fields yields three `FIELD_CHANGE` rows sharing one
changeset id, and a WhatsApp template renders with real lead values in preview.

---

## M6 — Lead list, filters, history filters *(8–10 days)*

*Expanded — was 6–8. Adds the history predicates.*

- List endpoint with quick filters, pagination, column hydration, sorting limited
  to indexed fields
- Filter DSL compiled to parameterised SQLAlchemy, nested AND/OR, operators from
  the M2 registry
- **History predicates — the significant addition:**
  - `action_performed` — type, actor, time window, count (`EXISTS` over `actions`)
  - `action_not_performed` — the "no contact in 14 days" case (`NOT EXISTS`)
  - `status_changed` — `from` and/or `to`, within a window
  - `assignee_changed` — `from` and/or `to`, within a window
- Search over `search_vector` + trigram on identity
- `saved_filters` CRUD, **sharing** (personal / shared / role-scoped), stats,
  reorder; `table_layouts` per member per filter
- Frontend: TanStack Table, column picker, visual filter builder that renders
  history predicates as first-class rules, not raw JSON

**Done when** "leads whose status went from HOT to Lost in the last 7 days" and
"leads with no outgoing call in 14 days" both work through the builder, and p95
stays under 300ms on the 50k demo workspace.

---

## M7 — Tasks, bulk, import/export *(7–9 days)*

*Expanded — was 5–7.*

- Tasks with buckets, timeline actions, **bulk task upload via Excel**
- Labels / lists
- Bulk edit ≤500, permission-checked per field, one changeset per run
- **Edit Report & Undo** — a bulk-operation log, and undo that reverses a
  changeset atomically by replaying inverse field changes
- Import: mapping limited to Import-granted + `show_in_import` fields, dry-run
  preview, commit
- **Excel Advance Distribution** — distribute an imported batch: round-robin,
  weighted, or availability-aware
- **Owner Specific Assignment** — assignee resolved from a sheet column
- **Excel Bulk Update** — update-by-identity flow, distinct from create
- **Import existing Actions** — historical timeline migration with a mapping UI
- Export honouring Export grants · duplicates + merge

**Done when** a bulk edit of 300 leads can be fully undone, and a historical
action import produces a coherent timeline.

---

## M8 — Assignment engine, sales groups, scheduler *(6–8 days)*

*New milestone.*

- **`sales_groups`** — named groups of members, used as distribution targets and
  report segments
- **Assignment rules** for incoming leads: round-robin · weighted · by field
  value · by sales group, all **availability-aware** (skip On Leave)
- Rule evaluation on lead create from any source — UI, import, intake API
- **Existing-leads distribution tool** — redistribute a filtered set
- **Scheduler** (`arq` cron) underpinning:
  - **Scheduled report email** — saved report + cadence + recipients
  - **Recurring-date greetings** — e.g. birthday wishes, using M5 templates
- Outbound email transport (SMTP / provider), needed by both

**Done when** a lead posted to the intake API is auto-assigned by the configured
rule and skips a rep marked On Leave, and a scheduled report arrives by email.

---

## M9 — Dashboards and reports *(6–8 days)*

*Expanded — was 4–5. Adds custom dashboards.*

- All report endpoints: leaderboard (honouring configured metrics), call report,
  activity, funnel, `breakdown?field_key=`, lead export
- Generic pivot widget — one component, configurable row dimension
- **Custom dashboards** — user-composed: add, remove, arrange, resize, configure
  widgets; saved per user
- **Role-specific dashboards** — a dashboard bound to a permission template, so
  every member on that template gets it
- Date ranges in the URL · drill-through with exact counts

**Read the `dataviz` skill before writing chart code.**

**Done when** an admin builds a dashboard, assigns it to the Caller template, and
a caller logs in to see it.

---

## M10 — Intake and event bus *(5–7 days)*

`POST /intake/leads` with API-key auth, identity matching, dedupe modes,
**unknown keys accepted with a warning** · assignment rules applied on intake ·
`intake_log` + viewer · transactional outbox + `arq` worker, HMAC signing,
backoff, DEAD after 8, redrive · `webhook_endpoints`, `api_keys` · payloads
respecting field permissions.

**Done when** killing the worker mid-delivery causes a retry, not a lost event.

---

## M11 — Hardening, provisioning, deploy *(6–8 days)*

Self-serve signup · perf pass at 500k leads across 5 workspaces · structlog with
request + workspace ids · Sentry · Prometheus (request duration, outbox depth,
intake rate, index-build queue, scheduler lag) · full permission matrix
re-verified · Playwright E2E proving *configure an empty workspace, then use it* ·
backups **plus a restore drill actually run** · staging + prod · operator runbook.

---

## Sequence and totals

```
M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9 → M10 → M11
                    │     │     └── history filters need M5's indexed payloads
                    │     └──────── changesets & templates gate M7 and M8
                    └────────────── gates every lead endpoint
```

| | Rev 1 | Rev 2 |
|---|---|---|
| Milestones | 11 | **12** |
| Working days | 53–73 | **71–94** |
| Calendar (solo) | 11–15 weeks | **14–19 weeks** |

Per-milestone: M0 2–3 · M1 6–8 · M2 6–8 · M3 4–6 · M4 7–9 · M5 8–10 · M6 8–10 ·
M7 7–9 · M8 6–8 · M9 6–8 · M10 5–7 · M11 6–8.

Still excludes campaigns, salesform, and the workflow engine.

---

## Definition of done — all milestones

- [ ] `ruff check`, `mypy app`, `pnpm lint`, `pnpm tsc --noEmit` clean
- [ ] `pytest` green; coverage not below the previous milestone
- [ ] Cross-workspace isolation test for every new endpoint
- [ ] Field-permission test for every new lead read/write path
- [ ] Alembic migration applies **and rolls back**
- [ ] No hardcoded business taxonomy in the diff
- [ ] Manually exercised against the demo workspace
