"""Blender-side bridge to the Chimera hub via ``zeno_thin``.

Imports are deferred so this module can be loaded even when the hub
packages haven't been pushed onto ``sys.path`` yet (the addon ``__init__``
inserts the repo root once Blender starts).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .bridge import addon_prefs


def _resolve_hub_python(prefs: Any) -> str | None:
    explicit = (getattr(prefs, "hub_python_exe", "") or "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    env = os.environ.get("CHIMERA_HUB_PYTHON", "").strip()
    if env and Path(env).is_file():
        return env
    root = os.environ.get("CHIMERA_ROOT", "").strip()
    if root:
        candidates = [
            Path(root) / "venv" / "bin" / "python",
            Path(root) / "venv" / "Scripts" / "python.exe",
            Path(root) / ".venv" / "bin" / "python",
            Path(root) / ".venv" / "Scripts" / "python.exe",
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
    return None


def hub_enabled() -> bool:
    prefs = addon_prefs()
    return bool(getattr(prefs, "use_hub_mode", False)) if prefs else False


def get_hub_client():
    """Return a ``ThinHubClient`` for the running hub, or ``None`` if unreachable.

    Never raises — UI/operator code uses ``None`` to fall back to in-process mode.
    """
    try:
        from zeno_thin import ThinHubClient, discover_session, ensure_hub_running
    except Exception:
        return None

    info = discover_session()
    if info is not None:
        return ThinHubClient(info)

    prefs = addon_prefs()
    py = _resolve_hub_python(prefs)
    if py is None:
        return None

    try:
        info = ensure_hub_running(python_exe=py, timeout_s=8.0)
    except Exception:
        return None
    return ThinHubClient(info)


__all__ = ["get_hub_client", "hub_enabled"]
