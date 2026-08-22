#!/usr/bin/env bash
# Take a backup (M11).
#
# `pg_dump --format=custom`, not plain SQL: custom is compressed, and it is the
# only format `pg_restore` can restore *selectively* — which is what you need at
# 3am when one table is wrong and the rest is fine.
#
#   ops/backup.sh [destination-directory]
#
# The companion is `ops/restore-drill.sh`. Run it. An untested backup is a
# belief, not a backup, and the failure mode is discovering that on the day it
# matters.
set -euo pipefail

DEST="${1:-./backups}"
SERVICE="${POSTGRES_SERVICE:-postgres}"
DB="${POSTGRES_DB:-crm}"
USER="${POSTGRES_USER:-crm}"

mkdir -p "$DEST"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$DEST/crm-$STAMP.dump"

echo "==> dumping $DB"
# Dumped inside the container so the client and server versions always agree —
# a newer pg_dump on the host against an older server is a class of failure
# that only shows up on somebody else's laptop.
docker compose exec -T "$SERVICE" \
  pg_dump --username="$USER" --dbname="$DB" --format=custom --no-owner --no-acl \
  > "$FILE"

SIZE=$(du -h "$FILE" | cut -f1)
echo "==> wrote $FILE ($SIZE)"

# A dump that cannot be listed cannot be restored, and finding that out now
# costs a second rather than an outage.
#
# Copied into the container rather than piped: `pg_restore` must *seek* to read
# a custom-format archive, and a pipe is not seekable — piping fails with "did
# not find magic string in file header" on a dump that is perfectly good, which
# is a memorably misleading way to lose an afternoon.
docker compose cp "$FILE" "$SERVICE:/tmp/verify.dump" > /dev/null
if ! docker compose exec -T "$SERVICE" pg_restore --list /tmp/verify.dump > /dev/null; then
  docker compose exec -T "$SERVICE" rm -f /tmp/verify.dump > /dev/null || true
  echo "!!  the dump is not readable by pg_restore — treat it as failed" >&2
  exit 1
fi
docker compose exec -T "$SERVICE" rm -f /tmp/verify.dump > /dev/null || true
echo "==> verified readable"
