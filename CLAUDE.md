# CLAUDE.md

Project context for Claude Code. Read this before every task.

---

## What this is

A **multi-tenant, fully configurable sales CRM** — a functional rebuild of
TeleCRM, sold to businesses. LevelUp Learning is **client #1**, not the product.

The single most important consequence:

> **Nothing about any customer's business is hardcoded.** No lead fields, no
> pipeline stages, no statuses, no call dispositions, no action types, no
> permission sets. Every one of those is created by a workspace admin through
> the settings UI at runtime.

If you find yourself typing a business term — `FORGE_WRITING`,
`INTERVIEW_SCHEDULED`, `application_status`, `mql` — into an enum, a migration,
or a seed file, **stop.** That is customer data, not product code.

The product is a schema engine with a CRM on top. `docs/03-configuration-model.md`
is the authoritative spec for what admins can configure. Read it first.

**Telephony is out of scope for v1.** No dialer, no recording, no CPaaS, no
mobile app. Calls are logged manually against workspace-configured dispositions.
Do not scaffold a provider interface "for later".

**An AI voice-agent integration is planned as a v2 differentiator.** Do not build
toward it now. The event bus in M9 is the seam it will attach to; that is enough.

---

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic |
| DB | PostgreSQL 16 |
| Background work | `arq` (Redis) |
| Object storage | S3-compatible (MinIO locally) — action file attachments |
| Frontend | React 18 + TypeScript, Vite |
| Data grid | TanStack Table v8 + TanStack Query v5 |
| UI | shadcn/ui + Tailwind |
| Forms | react-hook-form + zod, **schemas built at runtime from field definitions** |
| Backend tests | pytest, pytest-asyncio, httpx.AsyncClient, testcontainers |
| E2E | Playwright |
| Lint/format | ruff (py), eslint + prettier (ts) |
| Types | mypy strict on `app/`, `tsc --noEmit` on frontend |
| Local env | docker-compose: postgres, redis, minio, api, web |

Dependency management: **uv** for Python, **pnpm** for the frontend.

---

## Repo layout

```
/
├── CLAUDE.md
├── PROMPTS.md
├── docker-compose.yml
├── docs/
│   ├── 00-milestones.md          ← what to build, in order
│   ├── 01-data-model.md          ← schema
│   ├── 02-api-contract.md        ← endpoints
│   ├── 03-configuration-model.md ← THE SPEC. Read first.
│   └── 04-feature-coverage.md    ← the full TeleCRM feature audit
├── api/
│   ├── app/
│   │   ├── main.py, config.py, db.py
│   │   ├── tenancy/     ← workspace context, scoping, provisioning
│   │   ├── auth/        ← JWT, membership, permission resolution
│   │   ├── fields/      ← field definitions, type registries, validation
│   │   ├── models/      ← SQLAlchemy models
│   │   ├── schemas/     ← Pydantic request/response
│   │   ├── routers/     ← thin
│   │   ├── services/    ← business logic
│   │   ├── permissions/ ← field-level projection + write filtering
│   │   ├── events/      ← outbox, dispatcher, intake
│   │   └── seed/        ← demo workspace generation
│   └── tests/
└── web/
    └── src/
        ├── api/, components/, lib/, routes/
        └── features/
            ├── leads/, dashboard/, reports/
            ├── settings/    ← fields, stages, dispositions, actions, prefs
            └── permissions/ ← template editor incl. field matrix
```

---

## Architecture rules

Not suggestions. Violating them creates rework measured in weeks.

1. **Every table that holds customer data has `workspace_id`.** Every query
   filters on it. Use a session-level scoping dependency — never rely on callers
   remembering. Add a test that asserts cross-workspace reads return nothing.

2. **Configuration is data, not code.** Field types and action-field types are
   the only registries in code (13 and 8 respectively — `03-configuration-model.md`
   §1.3, §4.3). Everything else is rows.

3. **All lead reads pass through the field-projection service.** It removes
   fields the caller's permission template does not grant `View`. List, detail,
   export, API, webhooks — no exceptions. One implementation, one place.

4. **All lead writes pass through the write-filter service.** Fields without
   `Edit` are rejected with an error, never silently dropped.

5. **Every lead mutation writes an action** recording old and new value, in the
   same transaction. The timeline is the audit trail.

5a. **Every mutation opens a changeset.** Single edit, bulk edit, import,
   distribution, intake — all of them. Every action produced carries the
   `changeset_id`. This is what makes undo possible; it cannot be retrofitted.

5b. **`STAGE_CHANGE` and `ASSIGNMENT_CHANGE` payloads carry old and new ids**,
   with expression indexes on them. The history filters depend on it.

6. **The lead list endpoint never returns actions.**

7. **Field values live in JSONB keyed by field id.** There are no per-customer
   columns and no DDL at runtime. Sorting and filtering use expression indexes
   plus a small set of workspace-declared "indexed fields" (§2.4 of the data
   model).

8. **Outbound events go through a transactional outbox.** Never call an external
   webhook inside a request handler.

9. **All list endpoints are server-paginated.** Default 20, max 100.

10. **Timestamps are `timestamptz`, stored UTC**, rendered in the *workspace's*
    configured timezone — not the server's, not the browser's.

11. **Money carries the workspace currency.** `numeric(12,2)` plus a currency
    code. Never float, never assume INR.

12. **Phone normalisation uses the workspace's default country code.** Never
    hardcode `91`.

13. **Soft delete everywhere.** Config entities archive; leads and actions never
    hard-delete.

---

## Domain vocabulary

| Term | Meaning |
|---|---|
| **Workspace** | One customer tenant. Everything is scoped to it. |
| **Member** | A user's membership in a workspace, carrying a permission template |
| **Field definition** | An admin-created field. Two kinds: lead fields, action fields. |
| **Lead identity field** | The field designated unique per workspace (often Phone) |
| **Primary fields** | H1/H2 headline fields shown on cards and list rows |
| **Stage** | Pipeline position. Exactly one initial, N active, one won, one lost. |
| **Lost reason** | Required when a lead enters the lost stage. Max 25 per workspace. |
| **Call disposition** | Configurable call outcome. Exactly one is default. |
| **Custom action** | Admin-defined timeline event with its own fields, score, direction |
| **Score** | −1000..1000 per custom action; a lead's score is the sum of its actions |
| **Permission template** | Named permission set including the per-field View/Edit/Import/Export matrix |
| **Changeset** | One mutation batch. Every action it produced shares its id, so it can be undone as a unit. |
| **Message template** | Canned WhatsApp/SMS/email body with `{{field_key}}` substitution. Personal, shared, or role-scoped. |
| **Assignment rule** | Priority-ordered condition + strategy deciding who a new lead goes to |
| **Sales group** | Named set of members with weights; a distribution target and report segment |
| **Availability** | WORKING / ON_LEAVE / INACTIVE per membership. The assignment engine skips non-working members. |
| **History predicate** | A filter rule querying the timeline rather than current state |

---

## Conventions

- **API paths:** `/api/v1/workspaces/{workspace_id}/{resource}` for tenant data;
  `/api/v1/auth/*` and `/api/v1/me/*` are unscoped
- **Python:** snake_case, full type hints, no bare `Any` without a comment
- **TypeScript:** no `any`; derive types from the generated OpenAPI client
- **Migrations:** one Alembic revision per milestone. Never edit an applied one.
  **Never generate DDL at runtime.**
- **Errors:** `HTTPException` with `detail.code` and `detail.message`
- **Config:** env via `pydantic-settings`. No hardcoded secrets or URLs.

---

## Testing standards

- Every service function gets a unit test.
- Every endpoint gets happy-path, auth-failure, **and cross-workspace-isolation**
  tests.
- Every bug fix gets a failing regression test first.
- Real Postgres via testcontainers — JSONB and expression indexes don't behave
  the same on SQLite.
- Performance tests run against a demo workspace of **50,000 leads**.
  List endpoint p95 < 300ms.
- Field-permission tests are mandatory on every read and write path.

```bash
cd api && uv run ruff check . && uv run mypy app && uv run pytest
cd web && pnpm lint && pnpm tsc --noEmit && pnpm test
```

---

## How to work

- **One milestone at a time**, per `docs/00-milestones.md`. Don't start the next
  unprompted.
- **Don't build ahead.** Speculative abstraction is the main failure mode here —
  ironically, *on top of* a system that is itself all abstraction.
- **When the spec is ambiguous, ask.** Don't guess silently.
- **Commit per logical unit.** Conventional commits.
- **Never commit** `.env`, real customer data, or any real phone number or email.

---

## Known traps

- **Seeding a taxonomy is the #1 mistake here.** The demo seed creates a *fictional*
  workspace to exercise the engine. It is a fixture, not a default. New real
  workspaces get only: 4 built-in fields, 4 stages, a default lost-reason set, 7
  system call dispositions, 5 permission templates. Nothing industry-specific.
- **Field-level permissions cannot be retrofitted.** Build the projection service
  before the first lead endpoint.
- **`DEPENDENT_DROPDOWN`, `RECURRING_DATE`, and `LOCATION` are not scalars.**
  They need composite storage, cascade logic, and filter-builder support. Budget
  for them; don't treat them as text.
- **`Can use variable` fields must resolve templates at write time.** The legacy
  system stored unresolved `{{site_source_name}}` literals as real option values
  and corrupted its own taxonomy. Resolve or quarantine — never persist raw.
- **Feature flags must gate endpoints, not just navigation.** A disabled feature
  returns 403, it doesn't merely hide a menu item.
- **Stage cardinality is enforced**: one initial, one won, one lost, N active.
  Reject attempts to create a second won stage.
- **Four things must exist before the milestone that consumes them.** Changesets
  and indexed transition payloads land in M5, not M7/M6. Message templates land
  in M5, not M8. User availability lands in M1, not M8. Each was found missing in
  the `04-feature-coverage.md` audit precisely because it looked like a later
  feature and is actually an earlier dependency.
- **Undo must surface conflicts.** A lead edited after a changeset cannot be
  blindly reverted — report it and let the operator decide.
- **The scheduler runs in the workspace timezone**, not the server's, and
  scheduled reports render as their creating member so field permissions apply.
