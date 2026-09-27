#!/bin/sh
set -eu

if [ -n "${HERMES_BACKUP_TOKEN:-}" ] && [ -z "${HERMES_DASHBOARD_DRAIN_SECRET:-}" ]; then
  export HERMES_DASHBOARD_DRAIN_SECRET="$HERMES_BACKUP_TOKEN"
fi

if [ -n "${HERMES_BACKUP_URL:-}" ] && [ -n "${HERMES_BACKUP_TOKEN:-}" ]; then
  /usr/local/bin/hermes-backup restore || true
  /usr/local/bin/hermes-backup-watch &
fi

echo "Starting Hermes dashboard directly on port 10000..."
exec /opt/hermes/.venv/bin/hermes dashboard \
  --host 0.0.0.0 \
  --port 10000 \
  --no-open \
  --skip-build
