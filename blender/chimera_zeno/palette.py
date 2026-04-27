from __future__ import annotations

from typing import Any

import bpy

from zeno_ui.workflows import (
    refresh_palette_state,
    sanitize_palette_fields,
)

from .bridge import addon_prefs, make_client
from . import blender_qt
from . import hub_bridge
from . import launch_context as launch_context_mod


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
    asset_code: bpy.props.StringProperty(name="Asset Code", default="", options={"SKIP_SAVE"})
    version: bpy.props.StringProperty(name="Version", default="latest", options={"SKIP_SAVE"})

    _assets: list[dict[str, Any]] = []
    _versions: list[int] = []

    def _find_asset(self) -> dict[str, Any] | None:
        for a in self._assets:
            if str(a.get("code") or "").strip() == self.asset_code.strip():
                return a
        return None

    def _refresh(self) -> None:
        c = make_client()
        st = refresh_palette_state(
            c,
            project=self.project,
            query=self.query,
            asset_code=self.asset_code,
            version=self.version,
        )
        self._assets = st.assets
        self._versions = st.versions
        self.asset_code = st.asset_code
        self.version = st.version

    def invoke(self, context, event):  # pragma: no cover - Blender runtime
        prefs = addon_prefs()
        # 1) Hub mode: ask the hub to open its themed window — instant return,
        # no Qt setup inside Blender required.
        if hub_bridge.hub_enabled():
            client = hub_bridge.get_hub_client()
            if client is not None:
                try:
                    # Forward DCC state via launch_hint so the hub's palette
                    # knows *which* blend file to publish (and that it should
                    # use Blender canonicalization). Session hint already
                    # carries project/asset context — we augment it with the
                    # current file path.
                    hint: dict[str, Any] = {}
                    try:
                        base_hint = launch_context_mod.get_session_launch_hint()
                        if isinstance(base_hint, dict):
                            hint.update(base_hint)
                    except Exception:
                        pass
                    try:
                        blend_path = str(bpy.data.filepath or "").strip()
                        if blend_path:
                            hint["current_file"] = blend_path
                    except Exception:
                        pass
                    hint.setdefault("dcc", "blender")
                    client.open_palette(
                        prefs_default_project=(prefs.default_project if prefs else "") or "",
                        launch_hint=hint or None,
                    )
                    return {"FINISHED"}
                except Exception as exc:
                    self.report({"WARNING"}, f"Hub palette failed: {exc}; falling back.")

        # 2) In-DCC Qt fallback (PySide6 in Blender's Python).
        use_qt = getattr(prefs, "use_dashboard_qt_ui", True) if prefs else True
        if use_qt and blender_qt.show_command_palette_qt(operator=self):
            return {"FINISHED"}

        # 3) Native Blender dialog (always works, no extra deps).
        prefs_default = (prefs.default_project if prefs else "") or ""
        self.project = (self.project or prefs_default).strip()
        self.query = self.query.strip()
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
            version = (self.version or "latest").strip() or "latest"
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
        ac, ver = sanitize_palette_fields(self._assets, self.asset_code, self.version)
        self.asset_code = ac
        self.version = ver
        col = self.layout.column(align=True)
        col.label(text="Command Palette")
        col.label(text="Search assets, load versions, and publish")
        col.separator()
        col.prop(self, "project")
        col.prop(self, "query")
        col.prop(self, "action")
        col.prop(self, "asset_code")
        col.prop(self, "version")
        col.operator("chimera.palette_refresh", text="Refresh Results")
        if self._assets:
            preview = ", ".join(str(a.get("code") or "") for a in self._assets[:8] if str(a.get("code") or "").strip())
            if preview:
                col.label(text="Assets: " + preview)
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

