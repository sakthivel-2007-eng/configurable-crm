# What we're building — in plain English

For anyone joining this project. No jargon that isn't explained. Read this
first, then the specs will make sense.

---

## 1. What is a CRM, actually?

Imagine a sales team of ten people. Every day, hundreds of strangers show
interest in what the company sells — they fill in a form, click an ad, or send a
message. Each of those strangers is called a **lead**.

Someone has to:

- write down who they are
- decide which salesperson calls them
- remember what was said on every call
- notice who hasn't been called back
- work out who's close to buying and who's a waste of time

A **CRM** (Customer Relationship Management system) is the software that does all
of that. It's a shared notebook for the sales team that never forgets anything.

That's what we're building.

---

## 2. The one idea that makes this project different

There's an existing product called **TeleCRM** that does this well. Our client,
LevelUp Learning, uses it. We're building our own version — and selling it to
other companies too.

Here's the crucial part.

A school and a car dealership both need a CRM. But a school tracks *"which course
are they applying for?"* and *"interview date"*. A dealership tracks *"which car
model?"* and *"test drive booked"*. Completely different information.

**Bad approach:** build a CRM for schools. Then build a different one for
dealerships. Then another for gyms. You'd be building forever.

**Our approach:** build an **empty** CRM where the customer creates their own
fields, their own stages, their own everything — through the settings screen, by
clicking, without a programmer.

> Think of it like a kitchen. We build the kitchen — the oven, the fridge, the
> worktops. We don't stock the ingredients. Each restaurant that moves in brings
> their own.

This is why you'll see one rule repeated everywhere in the specs:

**Never write a business word into the code.**

No `FORGE_WRITING`. No `INTERVIEW_SCHEDULED`. Those belong to one customer. The
moment a customer's word appears in our code, we've built a tool for that one
customer instead of a product.

---

## 3. The words you'll see everywhere

Learn these eight and you can read any document in this repo.

**Workspace** — one customer's private space. LevelUp Learning gets one. The next
customer gets another. Like flats in an apartment building: same building, locked
doors, nobody can see into anyone else's.

**Lead** — one potential customer. A person who might buy.

**Field** — one blank on a form. "Phone number" is a field. "City" is a field.
Each customer invents their own.

**Field type** — what *kind* of blank it is. A date picker, a dropdown list, a
money amount, a checkbox. We support 13 kinds. The customer picks the kind; we
provide the kinds.

**Stage** — where a lead is in the journey. "New" → "Contacted" → "Interested" →
"Won" or "Lost". Again, each customer names their own steps.

**Action** — anything that happened to a lead. A call, a note, a status change, a
WhatsApp message. All the actions on a lead form a **timeline** — a diary of
everything anyone ever did. Nothing is ever deleted from it.

**Permission template** — a job-role badge. "Caller" opens some doors, "Manager"
opens more. You assign a badge to each staff member.

**Assignment rule** — the rota. When a new lead arrives at 2am, who gets it?
Round-robin? The person handling that product? These rules decide, automatically.

---

## 4. The clever bits (and why they matter)

Five parts of the design are unusual. Each exists for a real reason.

### Field-level permissions

Most systems say "this person can see leads" or "can't see leads". Ours goes
further: **per field**, per role, four separate switches — can they *view* it,
*edit* it, *import* it, *export* it?

So a junior caller might see a lead's phone number but not the deal value. And
might not be able to export anything at all — which stops someone downloading the
whole customer list on their last day.

*Why it matters to the build:* this has to be built into the foundations. You
can't add it later without rewriting everything that touches a lead. That's why
it's milestone 4 of 12, before any lead screen exists.

### Undo for bulk changes

A manager selects 300 leads and changes them all at once. Then realises it was
the wrong 300.

We group every batch of changes into a **changeset** — think of it as a receipt.
Undo reverses the whole receipt in one go.

The catch: if someone edited one of those leads *after* the bulk change, blindly
reversing it would destroy their work. So the system flags those as conflicts and
asks a human.

*Why it matters:* the receipt has to be created at the moment of the change. You
cannot invent receipts for purchases that already happened. So this is built in
milestone 5, even though undo itself arrives in milestone 7.

### Filters that ask about the past

Normal filter: *"show me leads marked HOT"* — a question about **right now**.

History filter: *"show me leads nobody has called in 14 days"* or *"leads that
went from HOT to Lost last week"* — questions about **what happened over time**.

The second kind is what a sales manager actually lives on. It's also much harder
to build, because the answer isn't sitting in a column — it has to be worked out
by searching through the timeline.

I missed this in the first version of the plan. It's in now.

### Message templates

A caller sends 60 WhatsApp messages a day. They are not typing each one.

So staff save reusable messages with blanks in them — *"Hi {{name}}, following up
about {{course}}"* — and the system fills in the blanks from that lead's details.

Neat detail: the filling-in respects permissions. If a caller isn't allowed to
see the deal value, a template can't sneak it out to a customer.

### The system doesn't automate — it announces

TeleCRM has a built-in automation builder: drag boxes, draw arrows, "when this
happens, do that". It's powerful and it is *enormous* to build — months on its
own.

We're not building it for version 1. Instead, whenever anything happens, our
system **announces it** to the outside world ("a lead was just marked Won"). A
separate tool the client already uses — **n8n** — listens for those announcements
and does the follow-up work.

*Result:* we get the same outcome for about a week of work instead of several
months. The automation builder can come later.

---

## 5. What we're deliberately NOT building yet

Being honest about this is more useful than pretending.

| Not in version 1 | Why |
|---|---|
| Phone calling from inside the app | Needs a mobile app and phone-network integrations. Staff log calls by hand for now. |
| The drag-and-drop automation builder | Months of work. n8n covers it. |
| Campaigns (calling queues) | Version 1.5. Staff work from saved filters instead. |
| Public sign-up forms | Version 1.5. |
| AI voice agent | Version 2 — this is the planned selling point later. |

---

## 6. The twelve steps

Each step produces something that works. Roughly a week or two each.

| Step | What gets built | In one sentence |
|---|---|---|
| **M0** | Skeleton | An empty app that starts up and says "I'm alive" |
| **M1** | Accounts | Companies, staff, logins, seat licences, who-reports-to-whom |
| **M2** | The field builder | Customers can invent their own information fields |
| **M3** | The pipeline builder | Customers can invent their own stages and call outcomes |
| **M4** | Permissions | Job-role badges, including the per-field switches |
| **M5** | Leads + timeline | Actual leads you can create, edit, and log activity against |
| **M6** | The list + filters | Find any lead, including the "who haven't we called?" questions |
| **M7** | Tasks, bulk, import/undo | Upload spreadsheets, change 300 at once, undo it |
| **M8** | Auto-assignment | New leads land on the right person's desk automatically |
| **M9** | Dashboards + reports | Charts, leaderboards, scheduled email reports |
| **M10** | Connections | Other systems can push leads in and get told when things change |
| **M11** | Launch prep | Speed, monitoring, backups, deployment |

**Order matters and cannot be shuffled.** You can't build leads (M5) before you
can define what a lead *is* (M2), and you can't do that before you know whose
leads they are (M1).

**First version worth demoing:** end of M6, roughly week 9.

**Full version 1:** about **14–19 weeks** for one full-time developer.

---

## 7. What's in each file

| File | What it is | Who reads it |
|---|---|---|
| **START-HERE.md** | How to run the project day-to-day | You, on day one |
| **EXPLAIN-SIMPLE.md** | This file | Anyone new |
| **CLAUDE.md** | The rules the AI coding assistant must never break | The AI, automatically, every time |
| **PROMPTS.md** | The exact instructions to paste, one per step | You, twelve times |
| **docs/00-milestones.md** | The twelve steps in detail, and how to tell when each is genuinely finished | You + the AI |
| **docs/01-data-model.md** | How information is stored — every table and column | The AI |
| **docs/02-api-contract.md** | Every button/action the software offers | The AI |
| **docs/03-configuration-model.md** | Exactly what customers can configure. **The most important document.** | Everyone |
| **docs/04-feature-coverage.md** | An honest checklist: all 80 things TeleCRM does, and whether we've planned each one | You, when scoping |

---

## 8. How the building actually works

We're using **Claude Code** — an AI that writes the software, guided by these
documents.

The loop is simple and repeats twelve times:

1. Paste the instruction for step M*n* from `PROMPTS.md`
2. Let it build
3. Check the result against the "done when" criteria in `00-milestones.md`
4. Run the automatic checks
5. Try it by hand
6. Save the work
7. Move to the next step

**One step at a time.** The most common way this goes wrong is the AI running
ahead and building step 7 while you're on step 3 — producing something that looks
finished and collapses the first time it's tested. `CLAUDE.md` explicitly forbids
this, and `PROMPTS.md` has ready-made corrections for when it happens anyway.

---

## 9. The honest summary

**What it is:** a sales CRM that each customer sets up themselves, with no
programmer involved.

**Why that's hard:** everything has to be flexible. There's no "status column" —
there's a system for *inventing* status columns.

**Why it's worth it:** build it once, sell it to any industry. LevelUp Learning
is customer number one, not the whole point.

**How long:** 14–19 weeks for one developer to reach a sellable version 1.

**Biggest risk:** the permission system and the field builder. Get either wrong
and it's not a patch — it's a rebuild.

**Biggest saving:** not building the automation engine. That decision alone keeps
this to months instead of a year.
