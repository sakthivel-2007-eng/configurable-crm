# 07 — Operator runbook

For whoever is on call. Written to be read at 3am by someone who did not build
this, so it says what to type and what the answer means.

Deployment is deliberately not covered: production images and hosting are
deferred, so everything here assumes the `docker compose` stack.

---

## 1. Is it up?

```bash
curl -s localhost:8000/health | jq .
```

`status: "ok"` plus `database`, `redis` and `object_storage` all `ok`. Anything
else names the failing dependency directly — the check exists so you do not have
to guess which of the three is down.

```bash
docker compose ps          # what is running
docker compose logs -f api # follow the API
```

---

## 2. Finding one request

Every response carries `X-Request-Id`, and every log line for that request
carries the same value — plus `workspace_id` once the request has resolved a
tenant. Ask a reporting customer for the id if their browser shows it; otherwise
search by path and time.

```bash
docker compose logs api | grep '"request_id":"<id>"'
```

`workspace_id` is the field that turns *"the CRM is slow"* into *"this tenant's
queries are slow"*, which are different problems with different fixes.

---

## 3. Webhooks are not arriving

Almost always the outbox, and almost always the consumer rather than us.

```sql
SELECT status, count(*) FROM outbox_events GROUP BY status;
```

- **PENDING piling up** — the worker is not running. `docker compose ps worker`.
- **FAILED with a `next_attempt_at` in the future** — backing off normally.
  `2^attempts` minutes capped at 60.
- **DEAD** — eight attempts over roughly four hours all failed. These are never
  retried automatically; somebody decides.

Look at what the consumer actually said:

```sql
SELECT event, attempts, last_status_code, last_error
FROM outbox_events WHERE status = 'DEAD' ORDER BY occurred_at DESC LIMIT 20;
```

Redrive from **Settings → Integrations → Outbound queue → Retry**, which resets
the attempt budget. Fix the consumer first; a redrive against a still-broken
endpoint just spends the budget again.

**Rows stuck in DELIVERING** are a worker that died mid-flight. They reclaim
themselves after five minutes — that is the designed recovery, not a fault. If
they are still there after ten, the worker is not running at all.

---

## 4. A customer says leads are missing

Check what actually arrived before checking what was stored:

**Settings → Integrations → Intake log**, filtered to rejections.

Rejections are logged too, which is the point — *"we posted it and nothing
arrived"* is the question this table exists to answer. An unknown field is
**accepted** and reported as a warning, never rejected, so a lead is never lost
to a field the workspace has not created yet. The common real rejection is an
unknown stage.

---

## 5. Reports or scheduled emails are late

```sql
SELECT name, cron, last_run_at, last_error
FROM scheduled_reports WHERE is_active ORDER BY last_run_at NULLS FIRST;
```

Cron is evaluated in the **workspace's** timezone, not the server's — a report
arriving "at the wrong time" is usually a timezone expectation, not a fault.

A schedule with `created_by` null cannot run at all: it renders as its creator so
field permissions apply, and that person has left. It needs recreating by
somebody current.

---

## 6. Sorting or filtering is slow on one field

Sorting uses expression indexes built by a worker for fields a workspace has
declared indexed.

```sql
SELECT f.key, i.status FROM indexed_fields i
JOIN lead_fields f ON f.id = i.field_id WHERE i.workspace_id = '<id>';
```

`PENDING` that never becomes `READY` means the index worker is not running.
Sorting on an undeclared field is refused with `400 field_not_indexed` — a
refusal, not a slow answer, because a sequential scan of 50,000 leads is not an
answer worth waiting for.

---

## 7. Somebody cannot sign in

The workspace model is **invite-only**. There is no registration; an account
exists because somebody invited it.

- **Never signed in** — the invitation link is single-use and expires after 7
  days. Re-invite, or have them use *Forgotten your password?*.
- **Link says it is not valid** — expired, already used, or superseded by a
  newer one. Issue a fresh one; that is the only remedy and it is cheap.
- **`403 no_license`** — the membership holds no licence. Team → grant one,
  subject to `seat_limit`.
- **`403 member_inactive`** — deactivated. Reactivate from Team.

The reset request answers identically whether or not an address has an account,
so "it said it sent one" is not evidence the account exists.

---

## 8. Backups

```bash
ops/backup.sh /var/backups/crm     # custom format, verified readable
ops/restore-drill.sh /var/backups/crm/crm-<stamp>.dump
```

The drill restores into a **scratch** database and compares row counts against
the live one, so it is safe to run against production. It also reads a lead's
JSONB back, because counts agreeing is necessary and not sufficient — a dump can
restore the right number of rows with the values unreadable.

**Run the drill on a schedule.** The common failure is not a missing backup; it
is a backup nobody ever restored.

Last drill run: 22 Aug 2026 — 50,000 leads and 241,403 actions, counts matched
and values readable.

---

## 9. Metrics

`/metrics` is **off by default** and unauthenticated when on, so it must sit
behind something that is not the public internet. Enable with
`METRICS_ENABLED=true`.

| Metric | What a rising number means |
|---|---|
| `crm_request_duration_seconds` | the obvious one, by route template |
| `crm_outbox_depth` | delivery is falling behind — usually a consumer is down |
| `crm_intake_requests_total` | intake volume, by outcome; watch the rejected label |
| `crm_index_build_queue` | the index worker has stopped; sorting will be refused |
| `crm_scheduler_lag_seconds` | reports are going out late |

Four of the five measure the asynchronous machinery. They are the ones worth
alerting on, because nothing about them is visible by looking at the app.

---

## 10. Things that are working as intended

Worth knowing before escalating:

- **A 404 for a record in another workspace.** Not a bug — a 403 would confirm
  the id exists.
- **A field missing from an export.** Field permissions apply to every read
  path, export included.
- **An unknown field accepted by intake.** Deliberate: a rejected payload at 2am
  is a lost lead. It appears as a warning in the intake log.
- **A webhook delivered twice.** Delivery is at-least-once. `X-CRM-Event-Id` is
  stable across retries so consumers dedupe on it.
- **An undo refusing to proceed.** A lead changed since the batch is reported
  rather than clobbered. The operator chooses.
