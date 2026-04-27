"""Lightweight DCC detection used by the Command Palette "Report Issue" flow.

We avoid importing any DCC module: we only inspect ``sys.modules`` and
environment hints. That keeps the helper safe to call from tests and from
DCCs whose Python interpreter doesn't have sibling DCC modules installed.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

_MODULE_TO_DCC: tuple[tuple[str, str], ...] = (
    ("bpy", "blender"),
    ("maya.cmds", "maya"),
    ("maya", "maya"),
    ("pymel", "maya"),
    ("hou", "houdini"),
    ("nuke", "nuke"),
    ("unreal", "unreal"),
    ("nuke_internal", "nuke"),
    ("c4d", "cinema4d"),
    ("rt", "3dsmax"),
    ("MaxPlus", "3dsmax"),
    ("substance_painter", "substance_painter"),
    ("sd", "substance_designer"),
    ("mari", "mari"),
)


def detect_dcc(modules: Optional[dict] = None, env: Optional[dict] = None) -> str:
    """Return a short DCC name (``blender``, ``maya``, ...) or ``'N/A'``.

    Accepts injectable ``modules`` / ``env`` maps for testability.
    """
    mods = modules if modules is not None else sys.modules
    environ = env if env is not None else os.environ

    for module_name, dcc in _MODULE_TO_DCC:
        if module_name in mods:
            return dcc

    for hint_var, needle_map in (
        ("CHIMERA_DCC", {}),
        ("ZENO_DCC", {}),
    ):
        value = (environ.get(hint_var) or "").strip().lower()
        if value:
            return value

    basename = os.path.basename(sys.executable or "").lower()
    if "blender" in basename:
        return "blender"
    if "maya" in basename:
        return "maya"
    if "houdini" in basename or basename.startswith("hfs"):
        return "houdini"
    if "nuke" in basename:
        return "nuke"
    if "unreal" in basename or "ue4editor" in basename or "ueeditor" in basename:
        return "unreal"

    return "N/A"
