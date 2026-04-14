from __future__ import annotations

bl_info = {
    "name": "Chimera Zeno",
    "author": "Chimera",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Chimera",
    "description": "Load/publish .blend via Chimera API and local cache",
    "category": "Pipeline",
}

import sys
from pathlib import Path

import bpy

_addon_keymaps: list[tuple[object, object]] = []


def _ensure_zeno_client_on_path() -> None:
    # Keep addon self-contained in dev: zeno_client package lives in zeno-plugin/zeno_client.
    here = Path(__file__).resolve()
    root = here.parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_zeno_client_on_path()

from . import launch_context as launch_context_mod
from . import navigator, operators_load, operators_publish, palette, preferences, ui_menus


def register() -> None:
    launch_context_mod.register_launch_context_prefs()
    preferences.register()
    operators_load.register()
    operators_publish.register()
    palette.register()
    navigator.register()
    ui_menus.register()

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm and wm.keyconfigs else None
    if kc:
        km = kc.keymaps.new(name="Window", space_type="EMPTY")
        kmi = km.keymap_items.new("chimera.palette_open", type="K", value="PRESS", ctrl=True)
        _addon_keymaps.append((km, kmi))


def unregister() -> None:
    for km, kmi in _addon_keymaps:
        km.keymap_items.remove(kmi)
    _addon_keymaps.clear()

    ui_menus.unregister()
    navigator.unregister()
    palette.unregister()
    operators_publish.unregister()
    operators_load.unregister()
    preferences.unregister()

