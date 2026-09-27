#!/bin/sh
set -eu

DATA_DIR="${HERMES_HOME:-/opt/data}"
BASE_URL="${HERMES_BACKUP_URL:-}"
TOKEN="${HERMES_BACKUP_TOKEN:-}"

[ -n "$BASE_URL" ] && [ -n "$TOKEN" ] || exit 0

case "${1:-}" in
  restore)
    archive="$(mktemp)"
    if curl -fsS \
      -H "Authorization: Bearer $TOKEN" \
      "$BASE_URL/backup/latest.tar.gz" \
      -o "$archive"; then
      tar -xzf "$archive" -C "$DATA_DIR"
      echo "Restored Hermes state from R2."
    fi
    rm -f "$archive"
    ;;

  save)
    work="$(mktemp -d)"
    mkdir -p "$work/data"

    # Copy ordinary state, but snapshot SQLite separately so WAL-mode data is consistent.
    tar -C "$DATA_DIR" \
      --exclude='./state.db' \
      --exclude='./state.db-wal' \
      --exclude='./state.db-shm' \
      --exclude='./logs/*' \
      --exclude='./cache/*' \
      -cf - . | tar -C "$work/data" -xf -

    if [ -f "$DATA_DIR/state.db" ]; then
      /opt/hermes/.venv/bin/python - "$DATA_DIR/state.db" "$work/data/state.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
PY
    fi

    tar -czf "$work/latest.tar.gz" -C "$work/data" .
    curl -fsS -X PUT \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/gzip" \
      --data-binary @"$work/latest.tar.gz" \
      "$BASE_URL/backup/latest.tar.gz"
    rm -rf "$work"
    echo "Backed up Hermes state to R2."
    ;;

  *)
    echo "usage: hermes-backup {restore|save}" >&2
    exit 2
    ;;
esac
