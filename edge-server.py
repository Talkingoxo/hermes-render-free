#!/opt/hermes/.venv/bin/python
from __future__ import annotations

import hmac
import json
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
import uvicorn

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

_ALLOWED_TOOLSETS = ["terminal", "file", "browser", "code_execution", "vision"]


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


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "hermes-native-executor",
        "ui": False,
        "platforms": ["telegram", "signal"],
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "hermes-native-executor", "ui": False}


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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), log_level="info")
