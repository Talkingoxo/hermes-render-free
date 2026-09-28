#!/opt/hermes/.venv/bin/python
from __future__ import annotations

import os
import shutil
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("HERMES_HOME", "/opt/data"))
ALLOWED = {"telegram", "signal"}

UNUSED_PLATFORM_KEYS = {
    "discord", "slack", "whatsapp", "whatsapp_cloud", "matrix", "mattermost",
    "email", "sms", "dingtalk", "feishu", "wecom", "wecom_callback", "weixin",
    "bluebubbles", "qqbot", "line", "simplex", "ntfy", "google_chat",
    "homeassistant", "photon", "teams", "webhook", "a2a", "irc", "yuanbao",
    "buzz", "api_server", "open_webui", "raft",
}

UNUSED_ENV_PREFIXES = (
    "DISCORD_", "SLACK_", "WHATSAPP_", "MATRIX_", "MATTERMOST_", "EMAIL_",
    "TWILIO_", "SMS_", "DINGTALK_", "FEISHU_", "WECOM_", "WEIXIN_",
    "BLUEBUBBLES_", "QQBOT_", "LINE_", "SIMPLEX_", "NTFY_", "GOOGLE_CHAT_",
    "HOMEASSISTANT_", "HASS_", "PHOTON_", "TEAMS_", "WEBHOOK_", "A2A_", "IRC_",
    "YUANBAO_", "BUZZ_", "API_SERVER_", "OPEN_WEBUI_", "RAFT_",
    "HERMES_DASHBOARD_",
)
UNUSED_ENV_KEYS = {"HERMES_DASHBOARD"}

changed = False


def clean_home(home: Path) -> None:
    global changed

    config = home / "config.yaml"
    if config.exists():
        raw = config.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            data = {}

        if "dashboard" in data:
            data.pop("dashboard", None)
            changed = True

        # The built-in Hermes browser needs fewer processes than Browser Use.
        browser = data.get("browser")
        if not isinstance(browser, dict):
            browser = {}
        if browser.get("backend") != "off" or browser.get("engine") != "auto":
            changed = True
        if browser.pop("cloud_provider", None) is not None:
            changed = True
        if browser.pop("use_gateway", None) is not None:
            changed = True
        browser["backend"] = "off"
        browser["engine"] = "auto"
        data["browser"] = browser

        gateway = data.get("gateway")
        if isinstance(gateway, dict):
            platforms = gateway.get("platforms")
            if isinstance(platforms, dict):
                kept = {k: v for k, v in platforms.items() if k in ALLOWED}
                if kept != platforms:
                    gateway["platforms"] = kept
                    changed = True

        top_platforms = data.get("platforms")
        if isinstance(top_platforms, dict):
            kept = {k: v for k, v in top_platforms.items() if k in ALLOWED}
            if kept != top_platforms:
                data["platforms"] = kept
                changed = True

        toolsets = data.get("platform_toolsets")
        if isinstance(toolsets, dict):
            kept = {
                k: v for k, v in toolsets.items()
                if k in ALLOWED or k == "cli"
            }
            if kept != toolsets:
                data["platform_toolsets"] = kept
                changed = True

        for key in list(data):
            if key in UNUSED_PLATFORM_KEYS:
                data.pop(key, None)
                changed = True

        rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        if rendered != raw:
            config.write_text(rendered, encoding="utf-8")
            changed = True

    env_file = home / ".env"
    if env_file.exists():
        old = env_file.read_text(encoding="utf-8")
        kept_lines = []
        for line in old.splitlines():
            stripped = line.strip()
            key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
            if key and (key in UNUSED_ENV_KEYS or key.startswith(UNUSED_ENV_PREFIXES)):
                changed = True
                continue
            kept_lines.append(line)
        new = "\n".join(kept_lines)
        if kept_lines:
            new += "\n"
        if new != old:
            env_file.write_text(new, encoding="utf-8")

    platforms_dir = home / "platforms"
    if platforms_dir.is_dir():
        for child in list(platforms_dir.iterdir()):
            if child.name not in ALLOWED:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)
                changed = True

    pairing_dir = home / "pairing"
    if pairing_dir.is_dir():
        for child in list(pairing_dir.iterdir()):
            name = child.name.lower()
            if name.startswith("_"):
                continue
            platform = name.split("-", 1)[0]
            if platform not in ALLOWED:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)
                changed = True


clean_home(ROOT)

profiles = ROOT / "profiles"
if profiles.is_dir():
    for profile in profiles.iterdir():
        if profile.is_dir():
            clean_home(profile)

print("Hermes config sanitized: Telegram and Signal only." if changed else "Hermes config already clean.")
raise SystemExit(42 if changed else 0)
