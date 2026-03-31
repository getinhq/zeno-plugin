from __future__ import annotations

import bpy

from .bridge import addon_prefs, build_asset_uri, heartbeat, make_cache, make_client


class CHIMERA_OT_load_asset(bpy.types.Operator):
    bl_idname = "chimera.load_asset"
    bl_label = "Chimera Load Asset"
    bl_description = "Resolve and cache asset URI, then open .blend"

    project: bpy.props.StringProperty(name="Project", default="")
    asset: bpy.props.StringProperty(name="Asset", default="")
    version: bpy.props.StringProperty(name="Version", default="latest")
    representation: bpy.props.StringProperty(name="Representation", default="blend")

    def execute(self, context):  # pragma: no cover - Blender runtime
        prefs = addon_prefs()
        project = (self.project or (prefs.default_project if prefs else "")).strip()
        asset = (self.asset or (prefs.default_asset if prefs else "")).strip()
        version = (self.version or "latest").strip()
        representation = (self.representation or "blend").strip()
        if not project or not asset:
            self.report({"ERROR"}, "Project and Asset are required.")
            return {"CANCELLED"}

        try:
            client = make_client()
            cache = make_cache()
            heartbeat(client, project=project, asset=asset)
            uri = build_asset_uri(project, asset, version, representation)
            local_path = cache.ensure_uri_cached(uri, client=client)
            self.report({"INFO"}, f"Cached: {local_path}")
            if prefs and prefs.open_after_load:
                bpy.ops.wm.open_mainfile(filepath=str(local_path))
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, f"Load failed: {e}")
            return {"CANCELLED"}


def register() -> None:
    bpy.utils.register_class(CHIMERA_OT_load_asset)


def unregister() -> None:
    bpy.utils.unregister_class(CHIMERA_OT_load_asset)

