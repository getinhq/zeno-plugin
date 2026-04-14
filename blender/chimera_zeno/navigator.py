from __future__ import annotations

from typing import Any

import bpy

from .bridge import make_client
from .launch_context import get_session_launch_hint


def _decode_project(value: str) -> tuple[str, str]:
    if "|" not in value:
        return "", ""
    pid, code = value.split("|", 1)
    return pid, code


def _project_items(self, context):  # pragma: no cover - Blender runtime
    try:
        c = make_client()
        projects = c.list_projects(status="active")
    except Exception:
        projects = []
    items: list[tuple[str, str, str]] = []
    for p in projects:
        pid = str(p.get("id") or "")
        code = str(p.get("code") or "")
        name = str(p.get("name") or code or pid)
        val = f"{pid}|{code}"
        label = f"{code}  {name}".strip()
        items.append((val, label, pid))
    return items or [("", "No projects", "No active projects")]


def _asset_items(self, context):  # pragma: no cover - Blender runtime
    scene = context.scene
    project_raw = str(getattr(scene, "zeno_nav_project", "") or "")
    pid, _ = _decode_project(project_raw)
    if not pid:
        return [("", "Select project first", "")]
    try:
        c = make_client()
        assets = c.list_assets(pid)
    except Exception:
        assets = []
    items: list[tuple[str, str, str]] = []
    for a in assets:
        code = str(a.get("code") or "")
        name = str(a.get("name") or code)
        items.append((code, f"{code}  {name}".strip(), code))
    return items or [("", "No assets", "No assets found")]


def _apply_launch_context_defaults(context) -> None:  # pragma: no cover - Blender runtime
    hint = get_session_launch_hint()
    if not hint:
        return
    scene = context.scene
    if getattr(scene, "zeno_nav_project", ""):
        return
    pid = str(hint.get("project_id") or "")
    pcode = str(hint.get("project_code") or "")
    if pid and pcode:
        target = f"{pid}|{pcode}"
        valid = {item[0] for item in _project_items(None, context)}
        if target in valid:
            scene.zeno_nav_project = target


class ZENO_OT_navigator_open(bpy.types.Operator):
    bl_idname = "zeno.navigator_open"
    bl_label = "Open Zeno Navigator"
    bl_description = "Browse project hierarchy and open assets"

    version: bpy.props.StringProperty(name="Version", default="latest")
    representation: bpy.props.StringProperty(name="Representation", default="blend")

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        _apply_launch_context_defaults(context)
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):  # pragma: no cover - Blender runtime
        scene = context.scene
        project_raw = str(getattr(scene, "zeno_nav_project", "") or "")
        asset_code = str(getattr(scene, "zeno_nav_asset", "") or "").strip()
        _, project_code = _decode_project(project_raw)
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
        col.prop(context.scene, "zeno_nav_project", text="Project")
        col.prop(context.scene, "zeno_nav_asset", text="Asset")
        col.prop(self, "version")
        col.prop(self, "representation")


class ZENO_PT_navigator(bpy.types.Panel):
    bl_label = "Zeno Navigator"
    bl_idname = "ZENO_PT_navigator"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Zeno"

    def draw(self, context):  # pragma: no cover - Blender runtime
        _apply_launch_context_defaults(context)
        col = self.layout.column(align=True)
        col.prop(context.scene, "zeno_nav_project", text="Project")
        col.prop(context.scene, "zeno_nav_asset", text="Asset")
        col.operator("zeno.navigator_open", text="Open Selected")


def register() -> None:
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
    bpy.utils.register_class(ZENO_PT_navigator)


def unregister() -> None:
    bpy.utils.unregister_class(ZENO_PT_navigator)
    bpy.utils.unregister_class(ZENO_OT_navigator_open)
    if hasattr(bpy.types.Scene, "zeno_nav_asset"):
        del bpy.types.Scene.zeno_nav_asset
    if hasattr(bpy.types.Scene, "zeno_nav_project"):
        del bpy.types.Scene.zeno_nav_project
