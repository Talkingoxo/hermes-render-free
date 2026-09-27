"""Authenticated native-tool bridge used by the single Cloudflare Hermes Worker."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hermes_cli.dashboard_auth.token_auth import register_token_route

router = APIRouter()

_PREFIX = "/api/plugins/hermes-edge-executor"
for _path in (f"{_PREFIX}/health", f"{_PREFIX}/tools", f"{_PREFIX}/execute"):
    register_token_route(_path)

_ALLOWED_TOOLSETS = ["terminal", "file", "browser", "code_execution", "vision"]


class ExecuteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = Field(default=None, max_length=256)
    session_id: str | None = Field(default=None, max_length=256)
    tool_call_id: str | None = Field(default=None, max_length=256)
    turn_id: str | None = Field(default=None, max_length=256)
    user_task: str | None = Field(default=None, max_length=20_000)


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


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "hermes-edge-executor",
        "toolsets": _ALLOWED_TOOLSETS,
    }


@router.get("/tools")
def tools() -> dict[str, Any]:
    definitions = _tool_definitions()
    return {
        "tools": definitions,
        "count": len(definitions),
    }


@router.post("/execute")
def execute(body: ExecuteRequest) -> dict[str, Any]:
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

    return {
        "ok": True,
        "tool": body.name,
        "result": parsed,
    }
