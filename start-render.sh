#!/bin/sh
set -eu

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
