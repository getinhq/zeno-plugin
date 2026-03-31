from __future__ import annotations

from typing import Any

import bpy

from .bridge import addon_prefs, filter_assets, make_client
from . import launch_context as launch_context_mod


def _asset_items(self, context):  # pragma: no cover - Blender runtime
    items = []
    for a in self._assets:
        aid = str(a.get("id") or "")
        code = str(a.get("code") or "")
        name = str(a.get("name") or "")
        items.append((aid, f"{code}  {name}".strip(), aid))
    return items or [("", "No assets", "No assets found")]


def _version_items(self, context):  # pragma: no cover - Blender runtime
    items = [("latest", "latest", "Resolve latest version")]
    for v in self._versions:
        sv = str(v)
        items.append((sv, sv, f"Version {sv}"))
    return items


class CHIMERA_OT_palette_open(bpy.types.Operator):
    bl_idname = "chimera.palette_open"
    bl_label = "Chimera Command Palette"
    bl_description = "Search assets, load versions, and publish"

    project: bpy.props.StringProperty(name="Project", default="")
    query: bpy.props.StringProperty(name="Search", default="")
    action: bpy.props.EnumProperty(
        name="Action",
        items=(
            ("load", "Load", "Load selected version"),
            ("publish", "Publish", "Publish current file to selected asset"),
            ("switch", "Version Switch", "Load selected explicit version"),
        ),
        default="load",
    )
    asset_id: bpy.props.EnumProperty(name="Asset", items=_asset_items)
    version: bpy.props.EnumProperty(name="Version", items=_version_items)

    _assets: list[dict[str, Any]] = []
    _versions: list[int] = []

    def _find_asset(self) -> dict[str, Any] | None:
        for a in self._assets:
            if str(a.get("id")) == self.asset_id:
                return a
        return None

    def _refresh(self) -> None:
        c = make_client()
        projects = c.list_projects(code=self.project)
        if not projects:
            self._assets = []
            self._versions = []
            return
        pid = str(projects[0].get("id") or "")
        assets = c.list_assets(pid)
        self._assets = filter_assets(assets, self.query)
        if not self._assets:
            self.asset_id = ""
            self._versions = []
            return
        if not self.asset_id:
            self.asset_id = str(self._assets[0].get("id") or "")
        asset = self._find_asset()
        if not asset:
            self._versions = []
            return
        groups = c.list_asset_version_groups(str(asset.get("id") or ""))
        nums: list[int] = []
        for g in groups:
            try:
                nums.append(int(g.get("version_number")))
            except Exception:
                pass
        nums.sort(reverse=True)
        self._versions = nums
        if self.version != "latest" and self.version:
            return
        self.version = str(nums[0]) if nums else "latest"

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        prefs = addon_prefs()
        self.project = (self.project or (prefs.default_project if prefs else "")).strip()
        hint = launch_context_mod.get_session_launch_hint()
        if hint and not self.project:
            if hint.get("project_code"):
                self.project = str(hint["project_code"]).strip()
            elif hint.get("project_id"):
                try:
                    c = make_client()
                    for p in c.list_projects():
                        if str(p.get("id")) == str(hint["project_id"]):
                            self.project = str(p.get("code") or "").strip()
                            break
                except Exception:
                    pass
        self.query = self.query.strip()
        try:
            self._refresh()
        except Exception:
            self._assets = []
            self._versions = []
        return context.window_manager.invoke_props_dialog(self, width=460)

    def execute(self, context):  # pragma: no cover - Blender runtime
        try:
            self._refresh()
            asset = self._find_asset()
            if not asset:
                self.report({"ERROR"}, "No asset selected.")
                return {"CANCELLED"}
            asset_code = str(asset.get("code") or "").strip()
            if not asset_code:
                self.report({"ERROR"}, "Selected asset has no code.")
                return {"CANCELLED"}
            if self.action == "publish":
                return bpy.ops.chimera.publish_current_file(
                    "INVOKE_DEFAULT",
                    project=self.project,
                    asset=asset_code,
                    version="next",
                    representation="blend",
                )
            version = self.version or "latest"
            return bpy.ops.chimera.load_asset(
                "INVOKE_DEFAULT",
                project=self.project,
                asset=asset_code,
                version=version,
                representation="blend",
            )
        except Exception as e:
            self.report({"ERROR"}, f"Palette failed: {e}")
            return {"CANCELLED"}

    def draw(self, context):  # pragma: no cover - Blender runtime
        col = self.layout.column(align=True)
        col.prop(self, "project")
        col.prop(self, "query")
        col.prop(self, "action")
        col.prop(self, "asset_id")
        col.prop(self, "version")
        col.operator("chimera.palette_refresh", text="Refresh Results")
        if self._versions:
            col.label(text="Known versions: " + ", ".join(str(v) for v in self._versions[:8]))


class CHIMERA_OT_palette_refresh(bpy.types.Operator):
    bl_idname = "chimera.palette_refresh"
    bl_label = "Refresh Chimera Palette"

    def execute(self, context):  # pragma: no cover - Blender runtime
        try:
            bpy.ops.chimera.palette_open("INVOKE_DEFAULT")
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, f"Refresh failed: {e}")
            return {"CANCELLED"}


def register() -> None:
    bpy.utils.register_class(CHIMERA_OT_palette_open)
    bpy.utils.register_class(CHIMERA_OT_palette_refresh)


def unregister() -> None:
    bpy.utils.unregister_class(CHIMERA_OT_palette_refresh)
    bpy.utils.unregister_class(CHIMERA_OT_palette_open)

