#!/opt/hermes/.venv/bin/python
from __future__ import annotations

import hmac
import hashlib
import secrets
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
import uvicorn

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

_ALLOWED_TOOLSETS = ["terminal", "file", "browser", "code_execution", "vision"]
_OMNIROUTE_URL = "http://127.0.0.1:20128"
_omniroute_lock = threading.Lock()
_omniroute_process: subprocess.Popen | None = None
_omniroute_internal_token = secrets.token_urlsafe(40)
_omniroute_api_key: str | None = None
_omniroute_bootstrapped = False


class ExecuteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = Field(default=None, max_length=256)
    session_id: str | None = Field(default=None, max_length=256)
    tool_call_id: str | None = Field(default=None, max_length=256)
    turn_id: str | None = Field(default=None, max_length=256)
    user_task: str | None = Field(default=None, max_length=20_000)


def _authorized(request: Request) -> bool:
    token = os.environ.get("HERMES_EDGE_TOKEN") or os.environ.get("HERMES_BACKUP_TOKEN") or ""
    supplied = request.headers.get("authorization", "")
    expected = f"Bearer {token}" if token else ""
    return bool(expected) and hmac.compare_digest(supplied, expected)


def _require_auth(request: Request) -> None:
    if not _authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _tool_definitions() -> list[dict[str, Any]]:
    from model_tools import get_tool_definitions

    return get_tool_definitions(
        enabled_toolsets=_ALLOWED_TOOLSETS,
        disabled_toolsets=[],
        quiet_mode=True,
        skip_tool_search_assembly=True,
    )


def _allowed_names() -> set[str]:
    return {
        item.get("function", {}).get("name", "")
        for item in _tool_definitions()
        if item.get("function", {}).get("name")
    }



def _omniroute_admin_request(path: str, method: str = "GET", data: dict | None = None) -> dict:
    headers = {"x-omniroute-internal-service-token": _omniroute_internal_token}
    payload = None
    if data is not None:
        headers["content-type"] = "application/json"
        payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(_OMNIROUTE_URL + path, data=payload, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)


def _bootstrap_free_provider() -> None:
    global _omniroute_bootstrapped, _omniroute_api_key
    if _omniroute_bootstrapped:
        return

    # OmniRoute's official free-onboarding API only accepts eligible keyless providers.
    candidates = _omniroute_admin_request("/api/providers/free-onboarding")
    allowed = {item.get("id") for item in candidates.get("providers", [])}
    if "opencode" in allowed:
        result = _omniroute_admin_request(
            "/api/providers/free-onboarding", "POST",
            {"providerIds": ["opencode"], "confirmed": True},
        )
        print("OmniRoute free provider setup result:",
              [x.get("status") for x in result.get("results", [])], flush=True)
        if not all(x.get("status") in ("created", "skipped") for x in result.get("results", [])):
            raise RuntimeError("OmniRoute keyless provider registration failed")
    else:
        print("OmniRoute keyless onboarding: OpenCode already configured or unavailable", flush=True)

    key_path = Path(os.environ.get("HERMES_HOME", "/opt/data")) / ".omniroute" / "hermes-local-api-key.json"
    if key_path.exists():
        try:
            saved = json.loads(key_path.read_text(encoding="utf-8")).get("key")
            if isinstance(saved, str) and saved:
                _omniroute_api_key = saved
        except (ValueError, OSError):
            pass

    if not _omniroute_api_key:
        response = _omniroute_admin_request(
            "/api/keys", "POST", {"name": "hermes-local-model-bridge"},
        )
        candidate = response.get("key")
        if not isinstance(candidate, str) or not candidate:
            raise RuntimeError("OmniRoute did not return a local inference key")
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(json.dumps({"key": candidate}) + "\n", encoding="utf-8")
        key_path.chmod(0o600)
        _omniroute_api_key = candidate

    _omniroute_bootstrapped = True
    print("OmniRoute local model bridge configured", flush=True)


def _setup_omniroute_once() -> None:
    try:
        _ensure_omniroute()
        print("OmniRoute commissioning test: ready", flush=True)
    except Exception as exc:
        print("OmniRoute commissioning test failed:", type(exc).__name__, flush=True)
    finally:
        _stop_omniroute()


@app.on_event("startup")
def _optional_omniroute_commissioning() -> None:
    if os.environ.get("HERMES_SETUP_OMNIROUTE_ONCE") == "1":
        threading.Thread(target=_setup_omniroute_once, name="omniroute-setup",
                         daemon=True).start()


def _omniroute_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{_OMNIROUTE_URL}/healthz", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def _ensure_omniroute() -> None:
    global _omniroute_process

    # On Render Free, the full OmniRoute Next.js server exceeds 512 MiB.
    # Keep it installed, but require explicit opt-in on a larger instance.
    if os.environ.get('HERMES_ALLOW_LOCAL_OMNIROUTE') != '1':
        raise HTTPException(status_code=503, detail='OmniRoute is installed but disabled on Render Free due to its 512 MB limit. Use Cloudflare Workers AI for testing.')

    if _omniroute_alive():
        _bootstrap_free_provider()
    return

    with _omniroute_lock:
        if _omniroute_alive():
            _bootstrap_free_provider()
        return

        log_dir = Path(os.environ.get("HERMES_HOME", "/opt/data")) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "omniroute.log", "ab", buffering=0)

        env = os.environ.copy()
        env["HOME"] = os.environ.get("HERMES_HOME", "/opt/data")
        env["OMNIROUTE_SERVER_HOST"] = "127.0.0.1"
        env["OMNIROUTE_INTERNAL_SERVICE_TOKEN"] = _omniroute_internal_token
        seed = os.environ.get("HERMES_BACKUP_TOKEN") or os.environ.get("HERMES_EDGE_TOKEN")
        if seed:
            env.setdefault("JWT_SECRET", hashlib.sha256((seed + "/omniroute/jwt").encode()).hexdigest())
            env.setdefault("API_KEY_SECRET", hashlib.sha256((seed + "/omniroute/api-key").encode()).hexdigest())
            env.setdefault("INITIAL_PASSWORD", hashlib.sha256((seed + "/omniroute/login").encode()).hexdigest())
        env["PORT"] = "20128"
        env["OMNIROUTE_PORT"] = "20128"
        env["OMNIROUTE_DISABLE_BACKGROUND_SERVICES"] = "true"
        env["NODE_OPTIONS"] = "--max-old-space-size=256"
        env["OMNIROUTE_MEMORY_MB"] = os.environ.get("OMNIROUTE_MEMORY_MB", "256")

        _omniroute_process = subprocess.Popen(
            ["omniroute", "--no-open", "--port", "20128"],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        deadline = time.time() + 100
        while time.time() < deadline:
            if _omniroute_alive():
                _bootstrap_free_provider()
            return
            if _omniroute_process.poll() is not None:
                break
            time.sleep(1)

        exit_code = _omniroute_process.poll() if _omniroute_process is not None else None
        log_path = log_dir / "omniroute.log"
        raw_log = log_path.read_text(encoding="utf-8", errors="replace")[-15000:] if log_path.exists() else ""
        # Emit error classifications, not raw logs: logs can contain provider credentials.
        patterns = [
            "server not found", "better-sqlite3", "unsupported node", "eacces",
            "enoent", "out of memory", "heap out of memory", "segmentation fault",
            "bind", "port in use", "eaddrinuse", "error:", "fatal", "permission denied",
            "signal", "killed", "timed out", "database", "migration",
        ]
        detected = [name for name in patterns if name in raw_log.lower()]
        print(f"OmniRoute startup failed: exit_code={exit_code}, signals={detected}, log_bytes={len(raw_log)}", flush=True)
        _stop_omniroute()
        raise HTTPException(status_code=503, detail="OmniRoute failed to start; inspect authenticated diagnostics")


def _stop_omniroute() -> None:
    global _omniroute_process
    process = _omniroute_process
    _omniroute_process = None
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _proxy_omniroute(path: str, method: str, body: bytes | None = None) -> Response:
    _ensure_omniroute()
    target = f"{_OMNIROUTE_URL}/{path.lstrip('/')}"
    headers = {"accept": "application/json"}
    if _omniroute_api_key:
        headers["authorization"] = "Bearer " + _omniroute_api_key
    if body is not None:
        headers["content-type"] = "application/json"

    req = urllib.request.Request(target, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as upstream:
            data = upstream.read()
            return Response(
                content=data,
                status_code=upstream.status,
                media_type=upstream.headers.get_content_type() or "application/json",
            )
    except urllib.error.HTTPError as exc:
        data = exc.read()
        return Response(
            content=data,
            status_code=exc.code,
            media_type=exc.headers.get_content_type() if exc.headers else "application/json",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OmniRoute proxy error: {exc}") from exc
    finally:
        _stop_omniroute()


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "hermes-native-executor",
        "ui": False,
        "platforms": ["telegram", "signal"],
        "omniroute": "lazy",
    }


@app.get("/health")
@app.get("/api/status")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "hermes-native-executor",
        "ui": False,
        "platforms": ["telegram", "signal"],
    }


@app.get("/api/plugins/hermes-edge-executor/health")
def edge_health(request: Request) -> dict[str, Any]:
    _require_auth(request)
    return {
        "ok": True,
        "service": "hermes-native-executor",
        "toolsets": _ALLOWED_TOOLSETS,
    }


@app.get("/api/plugins/hermes-edge-executor/tools")
def tools(request: Request) -> dict[str, Any]:
    _require_auth(request)
    definitions = _tool_definitions()
    return {"tools": definitions, "count": len(definitions)}


@app.post("/api/plugins/hermes-edge-executor/execute")
def execute(body: ExecuteRequest, request: Request) -> dict[str, Any]:
    _require_auth(request)
    allowed = _allowed_names()
    if body.name not in allowed:
        raise HTTPException(status_code=403, detail=f"Tool {body.name!r} is not allowed or unavailable")

    from model_tools import handle_function_call

    result = handle_function_call(
        body.name,
        body.arguments,
        task_id=body.task_id,
        session_id=body.session_id,
        tool_call_id=body.tool_call_id,
        turn_id=body.turn_id,
        user_task=body.user_task,
        enabled_toolsets=_ALLOWED_TOOLSETS,
        disabled_toolsets=[],
    )

    parsed: Any = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            pass

    return {"ok": True, "tool": body.name, "result": parsed}


@app.get("/api/omniroute/diagnostics")
def omniroute_diagnostics(request: Request) -> dict[str, Any]:
    _require_auth(request)
    log_path = Path(os.environ.get("HERMES_HOME", "/opt/data")) / "logs" / "omniroute.log"
    content = log_path.read_text(encoding="utf-8", errors="replace")[-4000:] if log_path.exists() else "(no OmniRoute log)"
    process = _omniroute_process
    return {
        "installed": Path("/usr/local/bin/omniroute").exists(),
        "process_running": process is not None and process.poll() is None,
        "recent_log": content,
    }


@app.get("/api/omniroute/v1/models")
def omniroute_models(request: Request) -> Response:
    _require_auth(request)
    return _proxy_omniroute("v1/models", "GET")


@app.post("/api/omniroute/v1/chat/completions")
async def omniroute_chat(request: Request) -> Response:
    _require_auth(request)
    body = await request.body()
    return _proxy_omniroute("v1/chat/completions", "POST", body)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), log_level="info")
