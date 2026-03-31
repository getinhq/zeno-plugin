"""Blender: consume ZENO_LAUNCH_CONTEXT for navigator / open flow (strategy C — native UI)."""
from __future__ import annotations

from typing import Any, Optional

from zeno_client.launch_context import LaunchContextV1, read_launch_context_from_environ, apply_api_base_url_to_environ

_session: Optional[dict[str, Any]] = None


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
