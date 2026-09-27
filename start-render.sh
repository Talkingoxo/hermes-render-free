#!/bin/sh
set -eu

changed=0

if [ -n "${HERMES_BACKUP_URL:-}" ] && [ -n "${HERMES_BACKUP_TOKEN:-}" ]; then
  /usr/local/bin/hermes-backup restore || true
fi

set +e
/usr/local/bin/hermes-sanitize-config
sanitize_status=$?
set -e

if [ "$sanitize_status" -eq 42 ]; then
  changed=1
elif [ "$sanitize_status" -ne 0 ]; then
  echo "Hermes config sanitization failed with status $sanitize_status" >&2
  exit "$sanitize_status"
fi

if [ "$changed" -eq 1 ] && [ -n "${HERMES_BACKUP_URL:-}" ] && [ -n "${HERMES_BACKUP_TOKEN:-}" ]; then
  /usr/local/bin/hermes-backup save || true
fi

if [ -n "${HERMES_BACKUP_URL:-}" ] && [ -n "${HERMES_BACKUP_TOKEN:-}" ]; then
  /usr/local/bin/hermes-backup-watch &
fi

echo "Starting headless Hermes native executor on port ${PORT:-10000}..."
exec /usr/local/bin/hermes-edge-server
