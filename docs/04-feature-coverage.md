# Feature coverage audit

Every item from the four TeleCRM documentation screenshots (7 Aug 2026), mapped
against the plan as it stood before this audit.

**Headline: 80 items. 41 covered, 9 partial, 22 missing, 8 deliberately deferred.**

So — **no, the plan did not include all of TeleCRM's functionality.** About half.
The gaps are listed below without softening, because several of them are
architectural: they change the design, not just the backlog.

Legend: ✅ covered · ⚠️ partial · ❌ missing · ⏭ deferred by design (v1.5/v2)

---

## 1. Workspace Creation — 1/1 ✅

| Item | Status | Where |
|---|---|---|
| Create Workspace | ✅ | M1 provisioning |

---

## 2. Team and License — 1 ✅ · 1 ⚠️ · 5 ❌ · 1 ⏭

| Item | Status | Note |
|---|---|---|
| Add a Single User via Web App | ✅ | M1 `/members` |
| Configuring Team Hierarchy | ⚠️ | `memberships.manager_id` exists, but **no manager-sees-reports' -leads visibility rule** and no hierarchy UI |
| Bulk User Upload via Excel | ❌ | Not planned |
| Add a Single User via Mobile | ⏭ | No mobile app in v1 |
| How to assign a license | ❌ | **I dropped licensing as "SaaS vendor machinery". Wrong call** — you're the vendor now. Seat allocation is a product feature. |
| Delete User and Reassign Leads | ❌ | Deactivating a rep must reassign their pipeline. Real operational need. |
| Reactivate a Deleted User | ❌ | Not planned |
| Manage User's Availability | ❌ | Working / On Leave. **I saw this in the API connector data and didn't carry it across.** Feeds the assignment engine. |

---

## 3. Adding Leads — 3 ✅ · 1 ⚠️ · 3 ❌

| Item | Status | Note |
|---|---|---|
| Adding a Single Lead | ✅ | M5 |
| How to Upload an Excel | ✅ | M7 import |
| Excel Advance Distribution | ❌ | Distributing an imported batch across reps — round-robin, weighted, by availability |
| Owner Specific Assignment | ❌ | Assignment driven by a column in the sheet |
| Pitfalls of Excel Upload | ✅ | M7 dry-run preview |
| Excel Bulk Update | ⚠️ | Import can update by identity; no dedicated bulk-update flow spec'd |
| Importing existing Actions | ❌ | **Historical timeline migration.** Every customer switching CRMs needs it, and it appeared in LevelUp's own onboarding checklist. |

---

## 4. Campaigns — 0/4 ⏭

| Item | Status |
|---|---|
| Buttons in Campaigns · Filters in Campaign · How to create a campaign · Pitfalls in Campaign | ⏭ v1.5 |

Deliberate, but note campaigns are the *calling queue* for a telecalling team.
Shipping without them means reps work from filters instead. Defensible for v1;
worth confirming you agree.

---

## 5. Lead Fields — 12/12 ✅

Introduction · Text · Phone · Email · Number · Website · Money · Tags · Date ·
Check-Box · Dependent Dropdown · Pitfalls in dependent dropdown — all ✅ in M2.

The live UI also exposes **Dropdown** and **Location**, which the docs don't list.
The plan carries all 13. Live UI is authoritative.

---

## 6. Recurring Date — 2 ✅ · 1 ⚠️ · 1 ❌

| Item | Status | Note |
|---|---|---|
| Introduction | ✅ | M2 `RECURRING_DATE` |
| How to Create | ✅ | M2 |
| How to view Recurring Date | ⚠️ | Type exists; **no dedicated upcoming-occurrences view** |
| Birthday Wish | ❌ | Automated greeting on recurrence. Needs a scheduler + message send. |

---

## 7. Lead Stages — 2/2 ✅ · 8. Custom Configuration — 2/2 ✅

Lead Stages intro and status creation → M3. Custom call feedbacks → M3.
Labels or Lists → M7.

---

## 9. Filters — 5 ✅ · 1 ⚠️ · 4 ❌

| Item | Status | Note |
|---|---|---|
| All Lead Page | ✅ | M6 |
| Create & Share Filters | ⚠️ | `owner_id` nullable implies sharing; no sharing UI or permission rules spec'd |
| How to create Filters | ✅ | M6 |
| Delete a filter | ✅ | M6 |
| **Assignee change from and to** | ❌ | Filter on assignment *transitions* |
| **Status change from and to** | ❌ | Filter on status *transitions* |
| Assignee Filter | ✅ | M6 |
| Lead field filter | ✅ | M6 |
| **Filter your Actions** | ❌ | Filter leads by action history |
| **Action Performed Filter** | ❌ | Same family |

> **This is the worst miss in the audit.** My filter DSL only queries a lead's
> *current state*. Four of these ten filters query its *history* — "leads whose
> status went from HOT to Lost last week", "leads with no outgoing call in 14
> days". The legacy API I used for research had exactly this (`query_leads`
> accepted an `actions` block with type, performed_by and performed_at), and I
> read it and didn't carry it into the design.
>
> This changes the query compiler, not just the UI. History predicates compile to
> `EXISTS` / `NOT EXISTS` subqueries over `actions`, and transition predicates need
> `STATUS_CHANGE` payloads indexed on old and new value. Retrofitting it after M6
> means rewriting the DSL.

---

## 10. Reports — 7 ✅ · 1 ❌

| Item | Status |
|---|---|
| Leaderboard Report · Download Leaderboard Report · How to View Call Report · How to Download Call Report · Downloading Activity Report · Reporting in Detail · Leads report download | ✅ M8 |
| **Scheduling Reports Via Email** | ❌ Needs a scheduler and outbound email |

---

## 11. Custom Dashboard — 1 ⚠️ · 1 ❌

| Item | Status | Note |
|---|---|---|
| Create Admin Custom Dashboard | ⚠️ | I spec'd a generic pivot widget, but **not user-composed dashboards** — add, remove, arrange, configure widgets |
| Role Specific Custom Dashboard | ❌ | Dashboards bound to a permission template |

---

## 12. Templates — 0/3 ❌

| Item | Status |
|---|---|
| Introduction · Manage Personal Templates · Manage Role Specific Templates | ❌ |

**Entirely absent from the plan.** These are canned message templates
(WhatsApp / SMS / email), scoped personal or by role, with variable substitution
from lead fields. M5's WhatsApp compose is half a feature without them — a
telecaller sending 60 messages a day is not typing each one.

I saw "Templates" as a section in the permission-template editor, marked it
not-inspected, and never added it as a feature. That was sloppy.

---

## 13. Permission Templates — 3 ✅ · 3 ⚠️ · 2 ⏭

| Item | Status | Note |
|---|---|---|
| Introduction | ✅ | M4 |
| Lead specific rights | ✅ | M4 field matrix |
| Set up your lead view | ⚠️ | Noted the link, never spec'd. Per-role lead detail layout. |
| Salesform Specific Rights | ⏭ | Salesform is v1.5 |
| Team and Billing rights | ⚠️ | Group exists in `capabilities`; contents unspecified |
| Calling rights | ⚠️ | Same |
| Permission and rights | ✅ | M4 |
| Call Recording Accessibility | ⏭ | No telephony in v1 |

---

## 14. Task — 2 ✅ · 1 ❌

| Item | Status |
|---|---|
| How to schedule · Re-Schedule a task | ✅ M7 |
| **Bulk Upload Tasks** | ❌ Excel upload of tasks |

---

## 15. Lead Assignment — 1 ⚠️ · 1 ❌ · 1 ⏭

| Item | Status | Note |
|---|---|---|
| **Upcoming Leads Assignment** | ❌ | **Assignment rules for incoming leads** — round-robin, weighted, by availability, by field value |
| Existing Leads Assignment | ⚠️ | Bulk edit can reassign; no distribution tool |
| Leads Assignment in a Campaign | ⏭ | Campaigns v1.5 |

> Second architectural gap. A telecalling CRM without automatic distribution is a
> spreadsheet. This is a subsystem — rules, strategies, availability awareness,
> sales-group targeting — not a screen.

---

## 16. Bulk Edit — 1 ✅ · 1 ❌

| Item | Status | Note |
|---|---|---|
| How to a do Bulk Edit action | ✅ | M7 |
| **Edit Report & Undo** | ❌ | An audit report of bulk edits **with undo** |

Undo requires bulk operations to be a first-class **changeset** entity — a batch
id on every action it produced, so the whole set can be reversed atomically. That
must be designed into the action-writing service in M5, not bolted on later.

---

## 17. Sales Group — 0/1 ❌

| Item | Status |
|---|---|
| How to create Sales Group | ❌ |

I saw `Sales Group` as a feature flag in Preferences and never spec'd it. It's
team grouping used for distribution targeting and report segmentation.

---

## Summary

| Status | Count | Share |
|---|---|---|
| ✅ Covered | 41 | 51% |
| ⚠️ Partial | 9 | 11% |
| ❌ Missing | 22 | 28% |
| ⏭ Deferred by design | 8 | 10% |

### The four that change the architecture

Everything else on the missing list is additive — more screens, more endpoints.
These four are not, and each must land in or before the milestone that would
otherwise cement the wrong design:

1. **History-based filtering** → must be in the filter DSL from the start (M6, and
   the action payload indexes in M5)
2. **Assignment engine** → new subsystem; availability and sales groups are inputs
   (needs M1 user lifecycle first)
3. **Message templates** → new entity that M5's compose flows depend on
4. **Bulk-edit changesets with undo** → a batch id must exist in the
   action-writing service from M5

### Honest scope movement

The revised plan folds all 22 missing items in, adds a milestone for the
assignment engine and templates, and re-estimates:

| | Before | After |
|---|---|---|
| Milestones | 11 | 12 |
| Working days | 53–73 | **71–94** |
| Calendar (solo) | 11–15 weeks | **14–19 weeks** |

That is the cost of "every functionality", and it still excludes campaigns,
salesform, and the workflow engine.

### What I still haven't verified

These docs are a table of contents, not a specification. Before building the
newly-added features, open the actual pages for: Excel Advance Distribution,
Upcoming Leads Assignment, Edit Report & Undo, Manage User's Availability, Sales
Group, and the Templates section. I know *that* they exist and roughly what they
do; I do not know their exact rules. Each is one browser session away.
