#!/usr/bin/env bash
# Prove a backup restores (M11).
#
# `00-milestones.md` asks for "backups **plus a restore drill actually run**",
# and the emphasis is the point: the common failure is not a missing backup, it
# is a backup nobody ever restored. This restores into a *scratch* database and
# compares row counts against the live one, so it can be run against production
# data without touching production.
#
#   ops/restore-drill.sh path/to/crm-....dump
set -euo pipefail

DUMP="${1:?usage: ops/restore-drill.sh <dump-file>}"
SERVICE="${POSTGRES_SERVICE:-postgres}"
DB="${POSTGRES_DB:-crm}"
USER="${POSTGRES_USER:-crm}"
SCRATCH="${SCRATCH_DB:-crm_restore_drill}"

psql_live() { docker compose exec -T "$SERVICE" psql -U "$USER" -d "$DB" -tAc "$1"; }
psql_scratch() { docker compose exec -T "$SERVICE" psql -U "$USER" -d "$SCRATCH" -tAc "$1"; }

echo "==> restoring into $SCRATCH (the live database is not touched)"
# Copied in, not piped: a custom-format archive has to be seekable, and reading
# one from a pipe fails with a header error that reads like a corrupt backup.
docker compose cp "$DUMP" "$SERVICE:/tmp/drill.dump" >/dev/null
docker compose exec -T "$SERVICE" psql -U "$USER" -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null
docker compose exec -T "$SERVICE" psql -U "$USER" -d postgres -c "CREATE DATABASE $SCRATCH" >/dev/null
docker compose exec -T "$SERVICE" pg_restore --username="$USER" --dbname="$SCRATCH" \
  --no-owner --no-acl /tmp/drill.dump >/dev/null

echo "==> comparing"
FAILED=0
# The tables whose loss would actually end the business. Config can be rebuilt
# from a screen; leads and their timeline cannot be rebuilt from anything.
for TABLE in workspaces users memberships leads actions changesets; do
  LIVE=$(psql_live "SELECT count(*) FROM $TABLE")
  COPY=$(psql_scratch "SELECT count(*) FROM $TABLE")
  if [ "$LIVE" = "$COPY" ]; then
    printf '    %-14s %10s  ok\n' "$TABLE" "$COPY"
  else
    printf '    %-14s live=%s restored=%s  MISMATCH\n' "$TABLE" "$LIVE" "$COPY"
    FAILED=1
  fi
done

# Counts agreeing is necessary and not sufficient — a dump can restore the right
# number of rows with the JSONB unreadable. Read actual customer data back.
SAMPLE=$(psql_scratch "SELECT values->>'phone' FROM leads WHERE values ? 'phone' LIMIT 1")
if [ -z "$SAMPLE" ]; then
  echo "    lead values   UNREADABLE — counts matched but the data did not survive"
  FAILED=1
else
  echo "    lead values    readable  ok"
fi

echo "==> cleaning up"
docker compose exec -T "$SERVICE" psql -U "$USER" -d postgres -c "DROP DATABASE $SCRATCH" >/dev/null
docker compose exec -T "$SERVICE" rm -f /tmp/drill.dump >/dev/null || true

if [ "$FAILED" -ne 0 ]; then
  echo "==> DRILL FAILED — the backup does not reproduce the database" >&2
  exit 1
fi
echo "==> drill passed"
