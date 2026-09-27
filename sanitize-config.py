#!/opt/hermes/.venv/bin/python
from __future__ import annotations

import os
import shutil
from pathlib import Path

import yaml

HOME = Path(os.environ.get("HERMES_HOME", "/opt/data"))
CONFIG = HOME / "config.yaml"
ENV_FILE = HOME / ".env"
ALLOWED = {"telegram", "signal"}

UNUSED_PLATFORM_KEYS = {
    "discord", "slack", "whatsapp", "whatsapp_cloud", "matrix", "mattermost",
    "email", "sms", "dingtalk", "feishu", "wecom", "wecom_callback", "weixin",
    "bluebubbles", "qqbot", "line", "simplex", "ntfy", "google_chat",
    "homeassistant", "photon", "teams", "webhook", "a2a", "irc", "yuanbao",
    "buzz", "api_server",
}

UNUSED_ENV_PREFIXES = (
    "DISCORD_", "SLACK_", "WHATSAPP_", "MATRIX_", "MATTERMOST_", "EMAIL_",
    "TWILIO_", "SMS_", "DINGTALK_", "FEISHU_", "WECOM_", "WEIXIN_",
    "BLUEBUBBLES_", "QQBOT_", "LINE_", "SIMPLEX_", "NTFY_", "GOOGLE_CHAT_",
    "HOMEASSISTANT_", "PHOTON_", "TEAMS_", "WEBHOOK_", "A2A_", "IRC_",
    "YUANBAO_", "BUZZ_", "API_SERVER_",
)

changed = False

if CONFIG.exists():
    raw = CONFIG.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        data = {}

    if "dashboard" in data:
        data.pop("dashboard", None)
        changed = True

    gateway = data.get("gateway")
    if isinstance(gateway, dict):
        platforms = gateway.get("platforms")
        if isinstance(platforms, dict):
            kept = {k: v for k, v in platforms.items() if k in ALLOWED}
            if kept != platforms:
                gateway["platforms"] = kept
                changed = True

    for key in list(data):
        if key in UNUSED_PLATFORM_KEYS:
            data.pop(key, None)
            changed = True

    rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if rendered != raw:
        CONFIG.write_text(rendered, encoding="utf-8")
        changed = True

platforms_dir = HOME / "platforms"
if platforms_dir.is_dir():
    for child in platforms_dir.iterdir():
        if child.name not in ALLOWED:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
            changed = True

if ENV_FILE.exists():
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    kept_lines = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key and key.startswith(UNUSED_ENV_PREFIXES):
            changed = True
            continue
        kept_lines.append(line)
    new_text = "\n".join(kept_lines)
    if kept_lines:
        new_text += "\n"
    if new_text != ENV_FILE.read_text(encoding="utf-8"):
        ENV_FILE.write_text(new_text, encoding="utf-8")

print("Hermes config sanitized: Telegram and Signal only." if changed else "Hermes config already clean.")
raise SystemExit(42 if changed else 0)
