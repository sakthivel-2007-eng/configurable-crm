#!/bin/sh
# Apply migrations, then hand over to the container command.
# M0 has no revisions, so `upgrade head` is a no-op that proves Alembic is wired.
set -eu

echo "[entrypoint] alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting: $*"
exec "$@"
