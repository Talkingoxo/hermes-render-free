#!/bin/sh
set -eu

DATA_DIR="${HERMES_HOME:-/opt/data}"
BASE_URL="${HERMES_BACKUP_URL:-}"
TOKEN="${HERMES_BACKUP_TOKEN:-}"
[ -n "$BASE_URL" ] && [ -n "$TOKEN" ] || exit 0
CURL_COMMON="--connect-timeout 5 --max-time 90"

case "${1:-}" in
  restore)
    archive="$(mktemp)"
    echo "Restoring Hermes state from R2..."
    if curl -fsS $CURL_COMMON -H "Authorization: Bearer $TOKEN" \
      "$BASE_URL/backup/latest.tar.gz" -o "$archive"; then
      if tar -tzf "$archive" >/dev/null 2>&1; then
        tar -xzf "$archive" -C "$DATA_DIR"
        echo "Restored Hermes state from R2."
      else
        echo "Invalid R2 backup; keeping local state." >&2
      fi
    else
      echo "No backup restored; continuing startup." >&2
    fi
    rm -f "$archive"
    ;;
  save)
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    mkdir -p "$work/data"

    # Do not upload generated npm caches, downloaded browser binaries,
    # duplicate OmniRoute database backups, or operational logs.
    tar -C "$DATA_DIR" \
      --exclude='./state.db' \
      --exclude='./state.db-wal' \
      --exclude='./state.db-shm' \
      --exclude='./.omniroute/storage.sqlite' \
      --exclude='./.omniroute/storage.sqlite-wal' \
      --exclude='./.omniroute/storage.sqlite-shm' \
      --exclude='./logs' \
      --exclude='./cache' \
      --exclude='./.cache' \
      --exclude='./.npm' \
      --exclude='./.local/share' \
      --exclude='./.omniroute/logs' \
      --exclude='./.omniroute/cache' \
      --exclude='./.omniroute/db_backups' \
      --exclude='./.config/chromium' \
      --exclude='./.config/google-chrome' \
      --exclude='*/node_modules' \
      --exclude='*.crdownload' \
      --exclude='*.log' \
      -cf - . | tar -C "$work/data" -xf -

    for database in state.db .omniroute/storage.sqlite; do
      if [ -f "$DATA_DIR/$database" ]; then
        mkdir -p "$work/data/$(dirname "$database")"
        /opt/hermes/.venv/bin/python - "$DATA_DIR/$database" "$work/data/$database" <<'PY'
import sqlite3, sys
src = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
PY
      fi
    done

    tar -czf "$work/latest.tar.gz" -C "$work/data" .
    size="$(wc -c < "$work/latest.tar.gz" | tr -d ' ')"
    echo "Hermes state backup compressed size: $size bytes"
    if [ "$size" -gt 85000000 ]; then
      echo "Backup exceeds Cloudflare request limit; not replacing latest good R2 snapshot." >&2
      exit 3
    fi
    curl -fsS $CURL_COMMON -X PUT \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/gzip" \
      --data-binary @"$work/latest.tar.gz" \
      "$BASE_URL/backup/latest.tar.gz"
    echo "Backed up Hermes state to R2."
    ;;
  *)
    echo "usage: hermes-backup {restore|save}" >&2
    exit 2
    ;;
esac
