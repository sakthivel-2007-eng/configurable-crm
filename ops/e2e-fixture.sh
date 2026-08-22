#!/usr/bin/env bash
# Stand up an EMPTY workspace for the M11 end-to-end drill.
#
# `00-milestones.md` asks for a Playwright test proving *"configure an empty
# workspace, then use it"*. Every other spec in this repo runs against stubs;
# this one runs against the real API, so it needs a real database containing
# nothing but what provisioning creates — four built-in fields, four stages, a
# default lost-reason set, seven dispositions, five permission templates. No
# demo data, no taxonomy.
#
#   ops/e2e-fixture.sh            # 1. build the empty workspace
#   # 2. start the API against it:
#   #    DATABASE_URL=postgresql+asyncpg://crm:crm_local_password@localhost:5433/crm_e2e \
#   #      uv run uvicorn app.main:app --port 8000
#   ops/e2e-fixture.sh --redeem   # 3. set the owner's password through the API
#   E2E_LIVE=1 pnpm test:e2e      # 4. run the suite, live specs included
#
# Split in two because the API cannot be running while its database is dropped,
# and cannot redeem an invitation before it is running.
#
# The password is set here rather than in the test because typing a password is
# not what the milestone is about. Configuring a workspace from nothing is.
set -euo pipefail

DB="${E2E_DB:-crm_e2e}"
EMAIL="${E2E_EMAIL:-founder@example.com}"
PASSWORD="${E2E_PASSWORD:-drill-password-for-the-e2e}"
API="${E2E_API:-http://localhost:8000}"
DSN="postgresql+asyncpg://crm:crm_local_password@localhost:5433/$DB"

REDEEM_ONLY=0
[ "${1:-}" = "--redeem" ] && REDEEM_ONLY=1

if [ "$REDEEM_ONLY" -eq 0 ]; then
echo "==> recreating $DB"
# WITH (FORCE) because an API left running against this database would
# otherwise hold the drop open — and the operator's next move is to point it
# here anyway.
docker compose exec -T postgres psql -U crm -d postgres \
  -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null
docker compose exec -T postgres psql -U crm -d postgres -c "CREATE DATABASE $DB" >/dev/null

echo "==> migrating"
(cd api && DATABASE_URL="$DSN" uv run alembic upgrade head >/dev/null)

echo "==> bootstrapping the first account"
LINK=$(cd api && DATABASE_URL="$DSN" uv run python -m app.bootstrap \
  --email "$EMAIL" --name "A Founder" --workspace "Empty Co" | grep set-password)
TOKEN="${LINK##*token=}"
echo "$TOKEN" > "/tmp/crm-e2e-token"
echo "==> empty workspace ready. Start the API against $DB, then re-run with --redeem."
exit 0
fi

TOKEN="$(cat /tmp/crm-e2e-token)"

echo "==> waiting for the API on $DB"
# The API must already be running against this database — start it with
# DATABASE_URL pointing here. Checked rather than assumed, because the failure
# otherwise surfaces as a confusing 401 in the browser.
for _ in $(seq 1 30); do
  curl -sf "$API/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -sf "$API/health" >/dev/null || { echo "!!  no API on $API" >&2; exit 1; }

echo "==> redeeming the invitation"
curl -sf -X POST "$API/api/v1/auth/password-reset/confirm" \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$TOKEN\",\"new_password\":\"$PASSWORD\"}" >/dev/null

echo "==> ready: $EMAIL / $PASSWORD  (workspace 'Empty Co', nothing configured)"
