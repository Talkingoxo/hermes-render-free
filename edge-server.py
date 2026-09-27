#!/opt/hermes/.venv/bin/python
from __future__ import annotations

import hmac
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


def _omniroute_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{_OMNIROUTE_URL}/v1/models", timeout=2) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def _ensure_omniroute() -> None:
    global _omniroute_process

    if _omniroute_alive():
        return

    with _omniroute_lock:
        if _omniroute_alive():
            return

        log_dir = Path(os.environ.get("HERMES_HOME", "/opt/data")) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "omniroute.log", "ab", buffering=0)

        env = os.environ.copy()
        env["HOME"] = os.environ.get("HERMES_HOME", "/opt/data")
        env["OMNIROUTE_HOST"] = "127.0.0.1"
        env["OMNIROUTE_MEMORY_MB"] = os.environ.get("OMNIROUTE_MEMORY_MB", "256")

        _omniroute_process = subprocess.Popen(
            ["omniroute", "--no-open", "--port", "20128"],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        deadline = time.time() + 45
        while time.time() < deadline:
            if _omniroute_alive():
                return
            if _omniroute_process.poll() is not None:
                break
            time.sleep(1)

        raise HTTPException(status_code=503, detail="OmniRoute failed to start")


def _proxy_omniroute(path: str, method: str, body: bytes | None = None) -> Response:
    _ensure_omniroute()
    target = f"{_OMNIROUTE_URL}/{path.lstrip('/')}"
    headers = {"accept": "application/json"}
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
