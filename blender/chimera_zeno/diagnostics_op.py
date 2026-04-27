"""Diagnostics operator: "Chimera: Check Qt Status".

Prints a block of diagnostic info to Blender's system console and surfaces a
one-line summary through ``self.report`` — intended to answer the question
"why am I still seeing the native dialog?" at a glance.
"""
from __future__ import annotations

import bpy

from . import pyside_provision


class CHIMERA_OT_check_qt_status(bpy.types.Operator):
    bl_idname = "chimera.check_qt_status"
    bl_label = "Chimera: Check Qt Status"
    bl_description = (
        "Print Qt/PySide diagnostics to Blender's system console and show a summary. "
        "Use this when the themed dialog still isn't appearing."
    )

    def execute(self, context):  # pragma: no cover - Blender runtime
        pyside_provision.ensure_pyside_on_path()
        diag = pyside_provision.diagnostics()

        print("=" * 72)
        print("[chimera] Qt diagnostics")
        print(f"  sys.executable        = {diag['python_executable']}")
        print(f"  python binary guess   = {diag['python_binary_guess']}")
        print(f"  sys.prefix            = {diag['python_prefix']}")
        print(f"  python version        = {diag['python_version'].splitlines()[0]}")
        print(f"  PySide6 origin        = {diag['PySide6']}")
        print(f"  PySide2 origin        = {diag['PySide2']}")
        print("  Searched site dirs:")
        for d in diag["search_dirs"]:
            print(f"    - {d}")
        print(f"  Last install state   = {diag['install_state']}")
        print("=" * 72)

        if diag["PySide6"] or diag["PySide2"]:
            self.report(
                {"INFO"},
                f"Qt available: PySide6={bool(diag['PySide6'])} PySide2={bool(diag['PySide2'])}. "
                "Dashboard UI should work. See system console for paths.",
            )
        else:
            self.report(
                {"WARNING"},
                "PySide6 NOT importable. Install via: "
                f"{diag['python_binary_guess']} -m pip install --user PySide6 "
                "— then run this check again. Full paths in system console.",
            )
        return {"FINISHED"}


class CHIMERA_OT_install_pyside(bpy.types.Operator):
    bl_idname = "chimera.install_pyside"
    bl_label = "Chimera: Install PySide6"
    bl_description = (
        "Launch a background 'pip install --user PySide6' using Blender's Python. "
        "Rerun 'Check Qt Status' once it finishes."
    )

    def execute(self, context):  # pragma: no cover - Blender runtime
        state = pyside_provision.ensure_pyside_async()
        if state.get("done") and state.get("ok"):
            self.report({"INFO"}, "PySide6 already available.")
        else:
            self.report(
                {"INFO"},
                f"PySide6 install started via {state.get('python', '(unknown)')}. "
                f"{state.get('message', '')}",
            )
        return {"FINISHED"}


def register() -> None:
    bpy.utils.register_class(CHIMERA_OT_check_qt_status)
    bpy.utils.register_class(CHIMERA_OT_install_pyside)


def unregister() -> None:
    bpy.utils.unregister_class(CHIMERA_OT_install_pyside)
    bpy.utils.unregister_class(CHIMERA_OT_check_qt_status)
