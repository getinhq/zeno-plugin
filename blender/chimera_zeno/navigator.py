from __future__ import annotations

from typing import Any

import bpy

from zeno_ui.workflows import (
    decode_project,
    list_assets_for_project,
    list_projects_for_navigator,
    navigator_launch_hint_enum,
)

from .bridge import addon_prefs, make_client
from . import blender_qt
from . import hub_bridge
from .launch_context import get_session_launch_hint


def _project_items(self, context):  # pragma: no cover - Blender runtime
    try:
        rows = list_projects_for_navigator(make_client())
    except Exception:
        rows = []
    items: list[tuple[str, str, str]] = []
    for row in rows:
        label = f"{row['code']}  {row['name']}".strip()
        items.append((row["enum_value"], label, row["id"]))
    return items or [("", "No projects", "No active projects")]


def _asset_items(self, context):  # pragma: no cover - Blender runtime
    scene = context.scene
    project_raw = str(getattr(scene, "zeno_nav_project", "") or "")
    pid, _ = decode_project(project_raw)
    if not pid:
        return [("", "Select project first", "")]
    try:
        rows = list_assets_for_project(make_client(), pid)
    except Exception:
        rows = []
    items: list[tuple[str, str, str]] = []
    for row in rows:
        code = row["code"]
        label = f"{code}  {row['name']}".strip()
        items.append((row["enum_value"], label, code))
    return items or [("", "No assets", "No assets found")]


def _apply_launch_context_defaults(context) -> None:  # pragma: no cover - Blender runtime
    hint = get_session_launch_hint()
    if not hint:
        return
    scene = context.scene
    if getattr(scene, "zeno_nav_project", ""):
        return
    try:
        rows = list_projects_for_navigator(make_client())
    except Exception:
        rows = []
    target = navigator_launch_hint_enum(hint, rows)
    if target:
        scene.zeno_nav_project = target


class ZENO_OT_navigator_open(bpy.types.Operator):
    bl_idname = "zeno.navigator_open"
    bl_label = "Open Zeno Navigator"
    bl_description = "Browse project hierarchy and open assets"

    version: bpy.props.StringProperty(name="Version", default="latest")
    representation: bpy.props.StringProperty(name="Representation", default="blend")

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        _apply_launch_context_defaults(context)
        prefs = addon_prefs()
        if hub_bridge.hub_enabled():
            client = hub_bridge.get_hub_client()
            if client is not None:
                try:
                    client.open_navigator()
                    return {"FINISHED"}
                except Exception as exc:
                    self.report({"WARNING"}, f"Hub navigator failed: {exc}; falling back.")
        use_qt = getattr(prefs, "use_dashboard_qt_ui", True) if prefs else True
        if use_qt and blender_qt.show_navigator_qt(operator=self):
            return {"FINISHED"}
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):  # pragma: no cover - Blender runtime
        scene = context.scene
        project_raw = str(getattr(scene, "zeno_nav_project", "") or "")
        asset_code = str(getattr(scene, "zeno_nav_asset", "") or "").strip()
        _, project_code = decode_project(project_raw)
        if not project_code or not asset_code:
            self.report({"ERROR"}, "Select project and asset first.")
            return {"CANCELLED"}
        return bpy.ops.chimera.load_asset(
            "INVOKE_DEFAULT",
            project=project_code,
            asset=asset_code,
            version=(self.version or "latest").strip() or "latest",
            representation=(self.representation or "blend").strip() or "blend",
        )

    def draw(self, context):  # pragma: no cover - Blender runtime
        col = self.layout.column(align=True)
        col.label(text="Navigator")
        col.label(text="Browse project hierarchy and open assets")
        col.separator()
        col.prop(context.scene, "zeno_nav_project", text="Project")
        col.prop(context.scene, "zeno_nav_asset", text="Asset")
        col.prop(self, "version")
        col.prop(self, "representation")


def register() -> None:
    # Scene props are still required by the operator's own invoke_props_dialog
    # (its draw() reads context.scene.zeno_nav_project / zeno_nav_asset) even
    # though the 3D-view sidebar panel has been removed in favour of the
    # top-bar Zeno menu.
    bpy.types.Scene.zeno_nav_project = bpy.props.EnumProperty(
        name="Project",
        items=_project_items,
        options={"SKIP_SAVE"},
    )
    bpy.types.Scene.zeno_nav_asset = bpy.props.EnumProperty(
        name="Asset",
        items=_asset_items,
        options={"SKIP_SAVE"},
    )
    bpy.utils.register_class(ZENO_OT_navigator_open)


def unregister() -> None:
    bpy.utils.unregister_class(ZENO_OT_navigator_open)
    if hasattr(bpy.types.Scene, "zeno_nav_asset"):
        del bpy.types.Scene.zeno_nav_asset
    if hasattr(bpy.types.Scene, "zeno_nav_project"):
        del bpy.types.Scene.zeno_nav_project
