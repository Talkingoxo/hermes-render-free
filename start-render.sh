#!/bin/sh
set -eu

# Render wraps containers in a way that conflicts with Hermes' s6 PID-1 path.
# Run Hermes' own bootstrap directly, then hand off to its privilege-dropping
# main wrapper. This keeps all upstream initialization without requiring /init.
 /opt/hermes/docker/stage2-hook.sh
exec /opt/hermes/docker/main-wrapper.sh "$@"
