#!/opt/hermes/.venv/bin/python
import os
import subprocess
from pathlib import Path

from watchfiles import watch

DATA_DIR = Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve()
DEBOUNCE_MS = max(1_000, int(os.environ.get("HERMES_BACKUP_DEBOUNCE_SECONDS", "60")) * 1_000)
IGNORED_TOP_LEVEL = {"logs", "cache"}
IGNORED_NAMES = {"agent.log", "agent.log.1", "agent.log.2"}

def relevant(path: str) -> bool:
    try:
        rel = Path(path).resolve().relative_to(DATA_DIR)
    except Exception:
        return False
    if not rel.parts:
        return False
    if rel.parts[0] in IGNORED_TOP_LEVEL:
        return False
    if rel.name in IGNORED_NAMES:
        return False
    return True

print(f"Watching {DATA_DIR} for backup-worthy changes (debounce={DEBOUNCE_MS // 1000}s).", flush=True)

for changes in watch(
    DATA_DIR,
    recursive=True,
    debounce=DEBOUNCE_MS,
    step=1_000,
    raise_interrupt=False,
):
    if not any(relevant(path) for _, path in changes):
        continue
    try:
        subprocess.run(
            ["/usr/local/bin/hermes-backup", "save"],
            check=False,
            timeout=180,
        )
    except Exception as exc:
        print(f"Hermes backup watcher error: {exc}", flush=True)
