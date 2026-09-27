#!/opt/hermes/.venv/bin/python
import os
import queue
import subprocess
import threading
from pathlib import Path

from watchfiles import watch

DATA_DIR = Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve()
QUIET_SECONDS = max(1, int(os.environ.get("HERMES_BACKUP_DEBOUNCE_SECONDS", "60")))
IGNORED_TOP_LEVEL = {"logs", "cache"}
IGNORED_NAMES = {"agent.log", "agent.log.1", "agent.log.2"}

events: queue.Queue[object] = queue.Queue()

def relevant(path: str) -> bool:
    try:
        rel = Path(path).resolve().relative_to(DATA_DIR)
    except Exception:
        return False
    if not rel.parts:
        return False
    if rel.parts[0] in IGNORED_TOP_LEVEL:
        return False
    if rel.name in IGNORED_NAMES or rel.name.startswith(".spawn-ledger"):
        return False
    return True

def producer() -> None:
    for changes in watch(DATA_DIR, recursive=True, raise_interrupt=False):
        if any(relevant(path) for _, path in changes):
            events.put(object())

threading.Thread(target=producer, name="hermes-backup-events", daemon=True).start()
print(f"Watching {DATA_DIR} for backup-worthy changes (quiet period={QUIET_SECONDS}s).", flush=True)

while True:
    events.get()
    while True:
        try:
            events.get(timeout=QUIET_SECONDS)
        except queue.Empty:
            break

    try:
        result = subprocess.run(
            ["/usr/local/bin/hermes-backup", "save"],
            check=False,
            timeout=180,
        )
        if result.returncode != 0:
            print(f"Hermes backup exited with status {result.returncode}", flush=True)
    except Exception as exc:
        print(f"Hermes backup watcher error: {exc}", flush=True)
