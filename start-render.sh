#!/bin/sh
set -eu
echo "Starting Hermes dashboard directly on port 10000..."
exec /opt/hermes/.venv/bin/hermes dashboard \
  --host 0.0.0.0 \
  --port 10000 \
  --no-open \
  --skip-build
