#!/bin/sh
set -eu

# Keep browser daemon sockets isolated from other /tmp reapers.
export TMPDIR="${HERMES_HOME:-/opt/data}/.runtime/tmp"
mkdir -p "$TMPDIR"
chmod 700 "${HERMES_HOME:-/opt/data}/.runtime" "$TMPDIR"

changed=0

echo "Hermes headless startup beginning..."

if [ -n "${HERMES_BACKUP_URL:-}" ] && [ -n "${HERMES_BACKUP_TOKEN:-}" ]; then
  /usr/local/bin/hermes-backup restore || true
fi

echo "Sanitizing Hermes configuration..."
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

# The official Hermes image stages Chromium under /opt/hermes/tools.
# Our headless entrypoint bypasses its normal s6 stage2 bootstrap, so
# explicitly export the pinned executable path for browser-use/agent-browser.
if [ -z "${AGENT_BROWSER_EXECUTABLE_PATH:-}" ] && [ -r /etc/hermes/agent-browser-executable-path ]; then
  export AGENT_BROWSER_EXECUTABLE_PATH="$(cat /etc/hermes/agent-browser-executable-path)"
elif [ -z "${AGENT_BROWSER_EXECUTABLE_PATH:-}" ] && [ -x /usr/bin/chromium ]; then
  export AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium
fi
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/hermes/tools}"
if [ -n "${AGENT_BROWSER_EXECUTABLE_PATH:-}" ] && [ -x "$AGENT_BROWSER_EXECUTABLE_PATH" ]; then
  echo "Hermes packaged Chromium ready."
else
  echo "WARNING: Hermes packaged Chromium binary not detected." >&2
fi

echo "Starting headless Hermes native executor on port ${PORT:-10000}..."
exec /usr/local/bin/hermes-edge-server
