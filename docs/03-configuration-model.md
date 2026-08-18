# Configuration model

**The most important document in this repo.**

Nothing in this product is hardcoded. Every stage, field, status, disposition,
action type, and permission is created by a workspace admin through the UI. The
codebase ships **zero** business taxonomy — no `FORGE_WRITING`, no
`INTERVIEW_SCHEDULED`, no `application_status`. Those are things a *customer*
creates, not things we compile in.

Observed live in TeleCRM's Workspace Settings on 6 Aug 2026 (v189.1). Routes are
given so you can go back and look.

---

## 0. The settings surface

| Screen | Route | What it configures |
|---|---|---|
| Lead Fields | `/fields` | Lead schema — types, options, properties, identity |
| Lead Stage | `/lead-stage` | Pipeline — initial/active/won/lost, lost reasons |
| Call Feedback | `/call-feedback` | Call dispositions |
| Custom Actions | `/custom-action-definition` | User-defined timeline events, with their own form fields |
| Preferences | `/enterprise-preferences` | Locale, defaults, feature flags |
| Users | — | Team members |
| Permission Templates | `/all-permission-templates` | Named permission sets, incl. field-level RBAC |

All are scoped to a workspace. In our product every one of these is
`workspace_id`-scoped, with **no cross-workspace reads, ever.**

---

## 1. Lead Fields (`/fields`)

### 1.1 Screen structure

Three zones, top to bottom:

**Lead Id** — *which field is the unique identifier.* Currently `Phone`, with a
`Change` control. This is configurable per workspace, not a constant. A B2B
customer might key on Email; a dealership on registration number.

> Our earlier plan hardcoded phone as the natural key. Wrong. It's a setting.
> Dedup, intake matching, and merge all read from it.

**Primary Fields (Assign)** — `H1:` and `H2:`, currently `First Name` and
`Phone`. These are the headline fields rendered on lead cards, list rows, and the
detail header. Each has an edit control and a field picker.

**Other Fields** — everything else. 57 active in this workspace. Search box,
filter by type, and a view selector (`Active Fields` / hidden). Table columns:
`Field Name` · `Type` · `Created On` · `Last Modified` · `Actions (Edit | Hide)`.

Fields are **hidden, never deleted.**

### 1.2 Create / edit field

`+ Add a new Field` opens a drawer:

- **Name** — 1–40 characters (validated live)
- **Type** — see below
- **Description** — free text
- **Properties** — expandable (§1.4)

### 1.3 Field types — 13

| Type | Purpose |
|---|---|
| `TEXT` | Names, addresses, free text |
| `DROPDOWN` | One of a predefined list |
| `TAGS` | Many of a predefined list |
| `EMAIL` | Email addresses |
| `PHONE` | Contact numbers |
| `CHECKBOX` | Yes/no, true/false |
| `DATE` | Calendar dates |
| `MONEY` | Currency amounts |
| `NUMBER` | Numeric values |
| `WEBSITE` | URLs |
| `DEPENDENT_DROPDOWN` | Cascading — Country→State, Category→Subcategory |
| `RECURRING_DATE` | Repeating events — birthdays, renewals |
| `LOCATION` | City/state/landmark or GPS coordinates |

Three of these are more work than they look:

- **`DEPENDENT_DROPDOWN`** needs a parent-field reference and an option tree, plus
  cascade behaviour in every renderer, the filter builder, and import.
- **`RECURRING_DATE`** needs a recurrence rule and a "next occurrence" derivation
  for filtering and reminders.
- **`LOCATION`** is a composite (structured address + optional lat/lng), so it
  isn't a scalar in `values`.

Do not treat these as "just another dropdown/date/text". They earn their own
milestone slice.

### 1.4 Field properties

Four toggles, observed:

| Property | Effect |
|---|---|
| **Show in import** | Field appears as a mappable column in CSV/XLSX import |
| **Show in quick add** | Field appears in the fast lead-create form |
| **Lock after create** | Value becomes read-only once the lead exists |
| **Can use variable** | Value may contain template variables like `{{source}}` |

`Can use variable` explains the `{{site_source_name}}` and
`{{CUSTOM_ACTION_Lead Source}}` values found in LevelUp's live data — templates
that were never substituted. **Our implementation must resolve variables at write
time and reject or quarantine unresolved ones**, rather than storing the literal
template as an option value. That single decision prevents the taxonomy rot we
documented in the legacy workspace.

### 1.5 Dropdown / Tags option editor

When type is `DROPDOWN` or `TAGS`, an option builder appears:

- One row per option: **drag handle · colour swatch · label · remove**
- Label 1–70 characters
- `+` adds a row
- **`Copy options`** — clone the option set from another field
- **`+ Add multiple`** — bulk paste, one option per line

Colour is per option and drives the badge colour everywhere the value renders.

### 1.6 Built-in fields

Four exist in every new workspace: **Name, Phone, Email, Alternate Phone.**
These can be renamed and reordered but not deleted.

---

## 2. Lead Stages (`/lead-stage`)

### 2.1 Structure — this is not a flat list

A three-column pipeline joined by chevrons:

```
┌─ Initial stage ─┐   ┌──── Active stage ────┐   ┌──── Closed stage ────┐
│  NEW  [Default] │ → │  N stages, ordered,  │ → │  ┌ Won ┐  ┌ Lost ┐   │
│  (rename only)  │   │  each with a colour  │   │  │ one │  │ one  │   │
└─────────────────┘   └──────────────────────┘   │  └─────┘  └──────┘   │
                                                  │  Lost reasons (n/25)│
                                                  └──────────────────────┘
```

Cardinality, as observed:

| Zone | Count | Operations |
|---|---|---|
| Initial | exactly 1 | rename, recolour |
| Active | 0..n | add, rename, recolour, reorder, delete |
| Won | exactly 1 | rename, recolour |
| Lost | exactly 1 | rename, recolour |
| Lost reasons | 0..**25** | add, rename, reorder, delete |

> Our earlier model treated stages as a flat list with a `stage_type` tag and
> allowed many WON/LOST stages. That's wrong — enforce the cardinality above.
> The 25-reason cap is a real observed limit; adopt it or raise it deliberately.

### 2.2 Edit dialog

`Edit Status` modal: **Name** (max 28 chars, live counter) and **Choose Color**
(swatch picker). Cancel / Proceed.

### 2.3 Soft delete

Both lists keep archives, shown as collapsible footers: `Deleted statuses (9)`
and `Deleted reasons (2)`. Leads that referenced a deleted status keep it —
deletion removes it from the picker, not from history.

---

## 3. Call Feedback (`/call-feedback`)

Call dispositions, workspace-configurable.

> Header text, verbatim in effect: the **default** status is auto-assigned when
> call duration exceeds 0s, and the user may change it afterwards.

Observed set (7): `CONNECTED` *(default)* · `NUMBER BUSY` · `NO ANSWER` ·
`SWITCHED OFF` · `WRONG NUMBER` · `CALL LATER` · `REDIALED`

- Drag to reorder, `+` to add, `Archived status (n)` section below
- Per-item `⋮` menu: **Set default** · **Edit** · **Archive**
- `Edit` is disabled on system-generated statuses — *"can't edit system
  generated"*. So there are two tiers: **system** (archivable, not editable) and
  **custom** (fully editable).

Exactly one status carries the `default` badge at a time.

Coupled to Preferences → `Connected Call Minimum Duration (in sec)`, which is the
threshold that decides whether a call counts as connected.

**We have no telephony in v1**, so this drives *manual* call logging: the
disposition picker on the log-call form is populated from this list, with the
default preselected when the entered duration exceeds the threshold.

---

## 4. Custom Actions (`/custom-action-definition`)

A custom action is a **user-defined timeline event with its own form.** This is
the deepest configurability in the product.

### 4.1 List

Columns: `Name` · `Code` (numeric, e.g. `1025`) · `Score` · `View Leads` (opens
the lead list filtered to leads carrying this action). Tabs: `Active` /
`Archived`. Search by name or code.

Codes are workspace-scoped sequential integers starting at 1001.

> Note: only **manually-created** actions appear here. Actions whose source is a
> webhook or an outbound API template are managed under Automations. Same
> underlying entity, two management surfaces.

### 4.2 Create form

| Control | Notes |
|---|---|
| **Icon** | Icon picker |
| **Name** | Display name |
| **Score** | Integer, **min −1000 / max 1000** — contributes to lead scoring |
| **Direction** | `Inbound` · `Outbound` · `Information` |
| **Description** | Free text |
| **Allow Predated Actions** | Toggle — may this action be logged with a past timestamp? |
| **Fields(n)** | Nested field builder, `+Add field` |

Every action starts with one field: **`Notes`** (Text, required).

**Score** is the mechanism behind lead scoring — each logged action adds its
score to the lead's total. That's how the legacy `mql` numbers were produced.
Build scoring as a derived rollup over actions, not a manually-typed number.

### 4.3 Action field types — 8

Distinct from the lead field set. Observed:

`TEXT` · `NUMBER` · `DATE` · `DROPDOWN` · `TAGS` · **`USER`** · **`FILE`** ·
**`MEDIA_LINK`**

- `USER` — a picker over workspace members
- `FILE` — an upload, so actions need attachment storage
- `MEDIA_LINK` — a link to an audio/video asset (recordings, screen captures)

The create-field drawer is the **same component** as §1.2, with a different type
registry. Build one field-definition system with two registries, not two systems.

---

## 5. Preferences (`/enterprise-preferences`)

Four groups.

### Workspace Preferences

| Setting | Observed value | Type |
|---|---|---|
| Default Country Code | `91` 🇮🇳 | country picker |
| Default Timezone | `Asia/Kolkata` | IANA tz |
| Default Currency | `INR` | ISO 4217 |
| Connected Call Minimum Duration (in sec) | `1` | integer |
| Session Timeout | `Never` | enum |

These are the localisation seam. Phone normalisation uses the country code, all
timestamps render in the workspace timezone, and `MONEY` fields render in the
workspace currency. **A US customer must work without a code change.**

### Leaderboard

Which metrics appear on the leaderboard: `Lead Stage` (on), `Lead Rating` (off).

### Features — per-workspace feature flags

`Location Check-in` · `Campaign` (on) · `Custom Actions` (on) · `Sales Group` ·
`Lead Recapture` · `New Leads View` · `System Fields`

A whole module can be switched off per workspace. Navigation, routes, and
permissions must all respect these flags — a disabled feature is not merely
hidden, its endpoints must refuse.

### Sync Permissions

`Smart Syncing` (on). *[assumed: controls mobile sync scope — not inspected]*

---

## 6. Permission Templates (`/all-permission-templates`)

Named, reusable permission sets assigned to users. Far richer than a role enum.

### 6.1 List

5 defaults ship with a workspace: **Marketing · Admin · Caller · Manager · Root.**
Columns: `Name` · `Assigned to` (user count) · `Last modified on` · `Actions`.
Root is **view-only** (eye icon, no edit). `+ Add new` creates custom templates.

### 6.2 Editor

Five sections:

```
Assignee   — which users hold this template (with audit: created/modified by & when)
Access     — capability grants, grouped
View       — what the UI shows
Templates  — message/content templates          [not inspected]
Embedded Apps — third-party embeds              [not inspected]
```

**Access groups (10):** Leads · Salesform · Team · Permissions · Calling ·
Reports · Automations · Tasks · Billings · Integrations

**View groups (3):** Lead · Dashboard · Leads Table

### 6.3 Inside Access → Leads

- **Admin Access** — master toggle at the top
- **User Access** accordions:
  - `Create leads from Whatsapp and Phone Calls`
  - `Add or update leads/modify fields` — with a `Setup lead view` link
  - `Actions`
  - `Merge leads`
  - `Search`

Expanding `Add or update leads/modify fields` reveals:

- Toggles: **Manually add lead**, **Bulk Edit**
- **A field-level permission matrix**

### 6.4 The field-level permission matrix

This is the single most important thing in this document.

```
Fields          │ ☑ View (66) │ ☑ Edit (64) │ ☑ Import (64) │ ☐ Export (0)
                │    Full     │    Full     │     Full      │    None
────────────────┼─────────────┼─────────────┼───────────────┼──────────────
Age             │      ☑      │      ☑      │       ☑       │      ☐
Character Count │      ☑      │      ☑      │       ☑       │      ☐
City            │      ☑      │      ☑      │       ☑       │      ☐
Email           │      ☑      │      ☑      │       ☑       │      ☐
First Name      │      ☑      │      ☑      │       ☑       │      ☐
Phone           │      ☑      │      ☑      │       ☑       │      ☐
…
```

Per template, per field, four independent grants: **View · Edit · Import ·
Export.** Column headers carry select-all checkboxes, a filter dropdown, live
counts, and a rollup badge (`Full` / `Partial` / `None`). The field list has its
own search.

Implications you cannot retrofit later:

1. **Every lead read must project only View-permitted fields.** List, detail,
   export, API, webhook payloads — all of them.
2. **Every lead write must reject non-Edit fields**, silently dropping them is
   worse than erroring.
3. **Import mapping only offers Import-permitted fields.**
4. **Export omits non-Export fields**, and the observed default is `Export (0)
   None` — exporting is off by default for callers. That's a deliberate
   data-exfiltration control and we should match it.

Enforce this in **one** place — a field-projection service every read and write
passes through. Scattering it across endpoints guarantees a leak.

---

## 7. What this means for the build

The product is a **schema engine with a CRM on top**. Ordering follows:

1. Workspaces and provisioning
2. The field-definition system (both registries)
3. Stages, dispositions, custom actions
4. Permission templates including the field matrix
5. *Then* leads, timeline, list, dashboard — all of which read config at runtime

Building leads before configuration means retrofitting field-level permissions
across every endpoint. That retrofit is where this project would die.

**New-workspace provisioning** creates: 4 built-in fields, 1 initial stage,
1 won, 1 lost, a small default lost-reason set, the 7 system call dispositions,
and the 5 default permission templates. Nothing industry-specific. A new customer
sees an empty, working CRM and builds their own taxonomy — exactly as LevelUp
did.

---

## 8. Inspection status

| Area | Status |
|---|---|
| Lead Fields — screen, create form, 13 types, properties, option editor | **Verified** |
| Lead Stages — layout, cardinality, edit dialog, soft delete, 25-reason cap | **Verified** |
| Call Feedback — list, default rule, ⋮ menu, system vs custom | **Verified** |
| Custom Actions — list, create form, score, direction, 8 field types | **Verified** |
| Preferences — all four groups | **Verified** |
| Permission Templates — list, sections, Access groups, Leads matrix | **Verified** |
| Per-type property variations (e.g. Dependent Dropdown config UI) | **Not inspected** |
| Permission → Templates, Embedded Apps sections | **Not inspected** |
| Permission → Access: Salesform, Team, Calling, Reports, Automations, Tasks, Billings, Integrations | **Not inspected** — only Leads opened |
| Permission → View: Lead, Dashboard, Leads Table contents | **Not inspected** |
| Users screen | **Not inspected** |
| Smart Syncing semantics | **[assumed]** |

The not-inspected items are all *more* configuration of the same shape. They add
scope but no new architecture. Go back and capture them before building M4.
