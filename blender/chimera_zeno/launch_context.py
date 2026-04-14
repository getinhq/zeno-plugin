"""Blender: consume ZENO_LAUNCH_CONTEXT for navigator / open flow (strategy C — native UI)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

_session: Optional[dict[str, Any]] = None


@dataclass
class LaunchContextV1:
    version: str
    intent: str
    project_id: str
    project_code: Optional[str]
    asset_id: Optional[str]
    dcc: str
    api_base_url: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LaunchContextV1":
        return cls(
            version=str(data.get("version") or "1"),
            intent=str(data.get("intent") or ""),
            project_id=str(data.get("project_id") or ""),
            project_code=str(data.get("project_code") or "") or None,
            asset_id=str(data.get("asset_id") or "") or None,
            dcc=str(data.get("dcc") or "blender"),
            api_base_url=str(data.get("api_base_url") or "") or None,
        )


def read_launch_context_from_environ() -> Optional[LaunchContextV1]:
    raw = os.environ.get("ZENO_LAUNCH_CONTEXT")
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return LaunchContextV1.from_dict(obj)


def apply_api_base_url_to_environ(ctx: LaunchContextV1) -> None:
    if ctx.api_base_url:
        os.environ["ZENO_API_BASE_URL"] = ctx.api_base_url


def get_active_launch_context() -> Optional[LaunchContextV1]:
    """Return parsed context or None."""
    ctx = read_launch_context_from_environ()
    if ctx:
        apply_api_base_url_to_environ(ctx)
    return ctx


def register_launch_context_prefs() -> None:
    """Remember launch context for panels/operators in this Blender session."""
    global _session
    ctx = get_active_launch_context()
    if ctx is None:
        _session = None
        return
    _session = {
        "project_id": ctx.project_id,
        "project_code": ctx.project_code or "",
        "asset_id": ctx.asset_id or "",
        "intent": ctx.intent,
        "dcc": ctx.dcc,
    }


def get_session_launch_hint() -> Optional[dict[str, Any]]:
    """Subset of launch context for UI (project/asset scope)."""
    return _session
